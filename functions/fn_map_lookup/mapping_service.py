"""
Mapping Service Core Logic
==========================

Provides deterministic material mapping from nesting descriptions to canonical codes.

Lookup Order (Precedence):
1. Check Mapping Override (LPO > PROJECT > CUSTOMER > PLANT)
2. Exact match in Material Master
3. If no match → create exception

All lookups are immutably logged to Mapping History.
"""

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class MappingResult:
    """Result of a material mapping lookup."""
    
    success: bool = False
    decision: str = "REVIEW"  # AUTO, OVERRIDE, MANUAL, REVIEW
    canonical_code: Optional[str] = None
    sap_code: Optional[str] = None
    uom: Optional[str] = None
    sap_uom: Optional[str] = None
    conversion_factor: Optional[float] = None
    not_tracked: bool = False
    history_id: Optional[str] = None
    exception_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class MaterialMasterEntry:
    """Cached entry from Material Master."""
    
    row_id: int
    nesting_description: str
    canonical_code: str
    default_sap_code: Optional[str] = None
    uom: Optional[str] = None
    sap_uom: Optional[str] = None
    conversion_factor: Optional[float] = None
    not_tracked: bool = False
    active: bool = True


class MappingService:
    """
    Material mapping service with caching.
    
    Thread-safe singleton pattern for caching Material Master data.
    Cache is refreshed periodically or on-demand.
    
    Usage:
        service = MappingService(smartsheet_client)
        result = service.lookup("aluminum tape", lpo_id="LPO-555", trace_id="abc123")
    """
    
    _instance: Optional["MappingService"] = None
    _lock = threading.Lock()
    
    # Cache settings
    CACHE_TTL_SECONDS = 300  # 5 minutes
    
    def __new__(cls, *args, **kwargs):
        """Thread-safe singleton."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, smartsheet_client: Any):
        """
        Initialize the mapping service.
        
        Args:
            smartsheet_client: SmartsheetClient instance for API calls
        """
        if self._initialized:
            return
        
        self._client = smartsheet_client
        self._material_master_cache: Dict[str, MaterialMasterEntry] = {}
        self._cache_timestamp: Optional[datetime] = None
        # FIX: Add override cache to prevent N+1 API calls (v1.6.4)
        self._override_cache: List[Dict] = []
        self._override_cache_timestamp: Optional[datetime] = None
        self._cache_lock = threading.Lock()
        self._initialized = True
        
        logger.info("MappingService initialized")
    
    def lookup(
        self,
        nesting_description: str,
        lpo_id: Optional[str] = None,
        project_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        ingest_line_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> MappingResult:
        """
        Look up canonical mapping for a nesting description.
        
        Lookup order:
        1. Override table (by scope precedence)
        2. Material Master exact match
        3. Create exception if no match
        
        Args:
            nesting_description: Raw description from nesting file
            lpo_id: Optional LPO ID for override lookup
            project_id: Optional project ID for override lookup
            customer_id: Optional customer ID for override lookup
            ingest_line_id: Unique ID for this lookup (for history)
            trace_id: Trace ID for distributed tracing
            
        Returns:
            MappingResult with canonical code and SAP code
        """
        trace_id = trace_id or str(uuid4())
        ingest_line_id = ingest_line_id or str(uuid4())
        
        # Normalize the description
        normalized = self._normalize_description(nesting_description)
        
        logger.debug(
            f"[{trace_id}] Looking up mapping for: '{nesting_description}' "
            f"-> normalized: '{normalized}'"
        )
        
        # Idempotency Check: Return existing result if ingest_line_id already processed
        existing_result = self._check_existing_history(ingest_line_id, trace_id)
        if existing_result:
            logger.info(f"[{trace_id}] Idempotent hit for line {ingest_line_id}")
            return existing_result
        
        result = MappingResult()
        
        try:
            # Step 1: Check overrides (if scope context provided)
            if lpo_id or project_id or customer_id:
                override_result = self._check_overrides(
                    normalized, lpo_id, project_id, customer_id
                )
                if override_result:
                    result.success = True
                    result.decision = "OVERRIDE"
                    result.canonical_code = override_result.get("canonical_code")
                    result.sap_code = override_result.get("sap_code")
                    result.history_id = self._log_history(
                        ingest_line_id, normalized, result, trace_id
                    )
                    return result
            
            # Step 2: Check Material Master
            entry = self._lookup_material_master(normalized)
            if entry:
                result.success = True
                result.decision = "AUTO"
                result.canonical_code = entry.canonical_code
                result.sap_code = entry.default_sap_code
                result.uom = entry.uom
                result.sap_uom = entry.sap_uom
                result.conversion_factor = entry.conversion_factor
                result.not_tracked = entry.not_tracked
                result.history_id = self._log_history(
                    ingest_line_id, normalized, result, trace_id
                )
                return result
            
            # Step 3: No match - create exception
            result.decision = "REVIEW"
            result.error = f"No mapping found for: {normalized}"
            result.exception_id = self._create_exception(
                ingest_line_id, nesting_description, trace_id
            )
            result.history_id = self._log_history(
                ingest_line_id, normalized, result, trace_id
            )
            
            logger.warning(
                f"[{trace_id}] No mapping for '{normalized}', "
                f"exception created: {result.exception_id}"
            )
            
        except Exception as e:
            logger.exception(f"[{trace_id}] Error during mapping lookup: {e}")
            result.error = str(e)
        
        return result
    
    def _normalize_description(self, description: str) -> str:
        """
        Normalize a nesting description for matching.
        
        Transformations:
        - Lowercase
        - Trim whitespace
        - Collapse multiple spaces
        - Remove special characters (except hyphen)
        """
        if not description:
            return ""
        
        # Lowercase and trim
        normalized = description.lower().strip()
        
        # Collapse multiple spaces
        normalized = re.sub(r'\s+', ' ', normalized)
        
        # Remove special characters except hyphen and space
        normalized = re.sub(r'[^\w\s\-]', '', normalized)
        
        return normalized
    
    def _lookup_material_master(
        self, 
        normalized_description: str
    ) -> Optional[MaterialMasterEntry]:
        """
        Look up entry in Material Master cache.
        
        Refreshes cache if stale.
        """
        self._ensure_cache_fresh()
        
        with self._cache_lock:
            return self._material_master_cache.get(normalized_description)
    
    def _ensure_cache_fresh(self) -> None:
        """Refresh Material Master cache if stale."""
        now = datetime.utcnow()
        
        if self._cache_timestamp:
            age = (now - self._cache_timestamp).total_seconds()
            if age < self.CACHE_TTL_SECONDS:
                return
        
        self._refresh_cache()
    
    def _refresh_cache(self) -> None:
        """Reload Material Master from Smartsheet."""
        logger.info("Refreshing Material Master cache...")
        
        try:
            from shared.logical_names import Sheet, Column
            
            rows = self._client.get_all_rows(Sheet.MATERIAL_MASTER)
            
            new_cache: Dict[str, MaterialMasterEntry] = {}
            
            for row in rows:
                cells = {c.get("columnId"): c.get("value") for c in row.get("cells", [])}
                
                # Get column IDs from manifest
                col_ids = self._get_material_master_column_ids()
                
                nesting_desc = cells.get(col_ids["NESTING_DESCRIPTION"], "")
                if not nesting_desc:
                    continue
                
                # Normalize the description for cache key
                normalized = self._normalize_description(str(nesting_desc))
                
                # Check if active
                active_val = str(cells.get(col_ids.get("ACTIVE"), "Yes")).lower()
                if active_val in ["no", "false", "0"]:
                    continue
                
                # Parse not_tracked
                not_tracked_val = str(cells.get(col_ids.get("NOT_TRACKED"), "No")).lower()
                not_tracked = not_tracked_val in ["yes", "true", "1"]
                
                # Parse conversion factor
                conv_factor = None
                conv_factor_val = cells.get(col_ids.get("CONVERSION_FACTOR"))
                if conv_factor_val:
                    try:
                        conv_factor = float(conv_factor_val)
                    except (ValueError, TypeError):
                        pass
                
                entry = MaterialMasterEntry(
                    row_id=row["id"],
                    nesting_description=normalized,
                    canonical_code=str(cells.get(col_ids["CANONICAL_CODE"], "")),
                    default_sap_code=cells.get(col_ids.get("DEFAULT_SAP_CODE")),
                    uom=cells.get(col_ids.get("UOM")),
                    sap_uom=cells.get(col_ids.get("SAP_UOM")),
                    conversion_factor=conv_factor,
                    not_tracked=not_tracked,
                    active=True,
                )
                
                new_cache[normalized] = entry
            
            with self._cache_lock:
                self._material_master_cache = new_cache
                self._cache_timestamp = datetime.utcnow()
            
            logger.info(f"Material Master cache refreshed: {len(new_cache)} entries")
            
        except Exception as e:
            logger.exception(f"Error refreshing Material Master cache: {e}")
            raise
    
    def _get_material_master_column_ids(self) -> Dict[str, int]:
        """Get column IDs for Material Master from manifest."""
        from shared.manifest import get_manifest
        
        manifest = get_manifest()
        columns = manifest["sheets"]["MATERIAL_MASTER"]["columns"]
        
        return {name: col["id"] for name, col in columns.items()}
    
    def _check_overrides(
        self,
        normalized_description: str,
        lpo_id: Optional[str],
        project_id: Optional[str],
        customer_id: Optional[str],
    ) -> Optional[Dict[str, str]]:
        """
        Check override table with scope precedence.
        
        Precedence: LPO > PROJECT > CUSTOMER > PLANT
        """
        from shared.logical_names import Sheet
        
        # Build scope checks in precedence order
        scope_checks = []
        if lpo_id:
            scope_checks.append(("LPO", lpo_id))
        if project_id:
            scope_checks.append(("PROJECT", project_id))
        if customer_id:
            scope_checks.append(("CUSTOMER", customer_id))
        
        if not scope_checks:
            return None
        
        try:
            # FIX: Use cached override data to prevent N+1 API calls (v1.6.4)
            rows = self._get_override_cache()
            col_ids = self._get_override_column_ids()
            
            for scope_type, scope_value in scope_checks:
                for row in rows:
                    cells = {c.get("columnId"): c.get("value") for c in row.get("cells", [])}
                    
                    # Check scope match
                    row_scope_type = str(cells.get(col_ids["SCOPE_TYPE"], "")).upper()
                    row_scope_value = str(cells.get(col_ids["SCOPE_VALUE"], ""))
                    row_nesting_desc = self._normalize_description(
                        str(cells.get(col_ids["NESTING_DESCRIPTION"], ""))
                    )
                    
                    # Check if active
                    active_val = str(cells.get(col_ids.get("ACTIVE"), "Yes")).lower()
                    if active_val in ["no", "false", "0"]:
                        continue
                    
                    # Check effective dates (v1.6.4 fix)
                    now_date = datetime.now().date()
                    
                    eff_from_str = str(cells.get(col_ids.get("EFFECTIVE_FROM"), "")).split("T")[0]
                    eff_to_str = str(cells.get(col_ids.get("EFFECTIVE_TO"), "")).split("T")[0]
                    
                    if eff_from_str:
                        try:
                            eff_from = datetime.strptime(eff_from_str, "%Y-%m-%d").date()
                            if now_date < eff_from:
                                continue
                        except ValueError:
                            pass # Ignore invalid dates
                            
                    if eff_to_str:
                        try:
                            eff_to = datetime.strptime(eff_to_str, "%Y-%m-%d").date()
                            if now_date > eff_to:
                                continue
                        except ValueError:
                            pass
                    
                    if (
                        row_scope_type == scope_type
                        and row_scope_value == scope_value
                        and row_nesting_desc == normalized_description
                    ):
                        return {
                            "canonical_code": cells.get(col_ids["CANONICAL_CODE"]),
                            "sap_code": cells.get(col_ids["SAP_CODE"]),
                        }
            
        except Exception as e:
            logger.warning(f"Error checking overrides: {e}")
        
        return None
    
    def _get_override_cache(self) -> List[Dict]:
        """
        Get cached override rows, refreshing if stale.
        Uses same TTL as Material Master cache.
        """
        from shared.logical_names import Sheet
        
        now = datetime.utcnow()
        
        if self._override_cache_timestamp:
            age = (now - self._override_cache_timestamp).total_seconds()
            if age < self.CACHE_TTL_SECONDS:
                return self._override_cache
        
        # Refresh cache
        try:
            self._override_cache = self._client.get_all_rows(Sheet.MAPPING_OVERRIDE)
            self._override_cache_timestamp = now
            logger.info(f"Override cache refreshed: {len(self._override_cache)} entries")
        except Exception as e:
            logger.warning(f"Error refreshing override cache: {e}")
            # Return stale cache if available
            if self._override_cache:
                return self._override_cache
            self._override_cache = []
        
        return self._override_cache
    
    def _get_override_column_ids(self) -> Dict[str, int]:
        """Get column IDs for Mapping Override from manifest."""
        from shared.manifest import get_manifest
        
        manifest = get_manifest()
        columns = manifest["sheets"]["MAPPING_OVERRIDE"]["columns"]
        
        return {name: col["id"] for name, col in columns.items()}
    
    def _check_existing_history(self, ingest_line_id: str, trace_id: str) -> Optional[MappingResult]:
        """
        Check if an entry already exists for this ingest line.
        Returns MappingResult if found.
        """
        from shared.logical_names import Sheet, Column
        
        try:
            # Look up in Mapping History by Ingest Line ID
            # Note: We use the logical column name passed to find_row
            row = self._client.find_row(
                Sheet.MAPPING_HISTORY, 
                Column.MAPPING_HISTORY.INGEST_LINE_ID, 
                ingest_line_id
            )
            
            if row:
                # Row found - reconstruct result
                # Keys in 'row' are Physical Column Names (Titles)
                # We assume standard naming: "Canonical Code", "SAP Code", "Decision"
                
                # Try to map common titles
                canonical = row.get("Canonical Code") or row.get("CanonicalCode") or ""
                sap = row.get("SAP Code") or row.get("SAPCode") or ""
                decision = row.get("Decision") or "AUTO"
                
                # SOTA: Try to retrieve conversion context if available (columns must exist in History)
                uom = row.get("Canonical UOM") or row.get("UOM")
                factor = None
                factor_val = row.get("Conversion Factor")
                if factor_val:
                    try:
                        factor = float(factor_val)
                    except (ValueError, TypeError):
                        pass

                logger.info(f"[{trace_id}] Found existing history for line {ingest_line_id}")
                
                return MappingResult(
                    success=True,
                    decision=decision,
                    canonical_code=canonical,
                    sap_code=sap,
                    uom=uom,
                    conversion_factor=factor,
                    history_id=str(row.get("History ID", "")),
                )
                
        except Exception as e:
            logger.warning(f"[{trace_id}] Error in history check: {e}")
            
        return None

    def _log_history(
        self,
        ingest_line_id: str,
        nesting_description: str,
        result: MappingResult,
        trace_id: str,
    ) -> str:
        """
        Log mapping decision to history table.
        
        Returns the History ID.
        """
        from shared.logical_names import Sheet
        
        history_id = str(uuid4())[:8]  # Short ID for readability
        
        try:
            col_ids = self._get_history_column_ids()
            
            row_data = {
                col_ids["HISTORY_ID"]: history_id,
                col_ids["INGEST_LINE_ID"]: ingest_line_id,
                col_ids["NESTING_DESCRIPTION"]: nesting_description,
                col_ids["CANONICAL_CODE"]: result.canonical_code or "",
                col_ids["SAP_CODE"]: result.sap_code or "",
                col_ids["DECISION"]: result.decision,
                col_ids["TRACE_ID"]: trace_id,
                col_ids["CREATED_AT"]: datetime.utcnow().isoformat(),
                col_ids["NOTES"]: result.error or "",
                
                # SOTA: Persist conversion context if columns exist
                col_ids.get("UOM"): result.uom,
                col_ids.get("CONVERSION_FACTOR"): result.conversion_factor,
            }
            
            self._client.add_row(Sheet.MAPPING_HISTORY, row_data)
            
        except Exception as e:
            logger.error(f"Error logging history: {e}")
        
        return history_id
    
    def _get_history_column_ids(self) -> Dict[str, int]:
        """Get column IDs for Mapping History from manifest."""
        from shared.manifest import get_manifest
        
        manifest = get_manifest()
        columns = manifest["sheets"]["MAPPING_HISTORY"]["columns"]
        
        return {name: col["id"] for name, col in columns.items()}
    
    def _create_exception(
        self,
        ingest_line_id: str,
        nesting_description: str,
        trace_id: str,
    ) -> str:
        """
        Create exception for unmapped material.
        
        Returns the Exception ID.
        """
        from shared.logical_names import Sheet
        
        exception_id = f"MAPEX-{str(uuid4())[:8]}"
        
        try:
            col_ids = self._get_exception_column_ids()
            
            row_data = {
                col_ids["EXCEPTION_ID"]: exception_id,
                col_ids["INGEST_LINE_ID"]: ingest_line_id,
                col_ids["NESTING_DESCRIPTION"]: nesting_description,
                col_ids["STATUS"]: "OPEN",
                col_ids["CREATED_AT"]: datetime.utcnow().isoformat(),
                col_ids["TRACE_ID"]: trace_id,
            }
            
            self._client.add_row(Sheet.MAPPING_EXCEPTION, row_data)
            
        except Exception as e:
            logger.error(f"Error creating exception: {e}")
        
        return exception_id
    
    def _get_exception_column_ids(self) -> Dict[str, int]:
        """Get column IDs for Mapping Exception from manifest."""
        from shared.manifest import get_manifest
        
        manifest = get_manifest()
        columns = manifest["sheets"]["MAPPING_EXCEPTION"]["columns"]
        
        return {name: col["id"] for name, col in columns.items()}
    
    def invalidate_cache(self) -> None:
        """Force cache refresh on next lookup."""
        with self._cache_lock:
            self._cache_timestamp = None
        logger.info("Material Master cache invalidated")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics for monitoring."""
        with self._cache_lock:
            age = None
            if self._cache_timestamp:
                age = (datetime.utcnow() - self._cache_timestamp).total_seconds()
            
            return {
                "entries": len(self._material_master_cache),
                "cache_age_seconds": age,
                "ttl_seconds": self.CACHE_TTL_SECONDS,
                "is_stale": age is None or age >= self.CACHE_TTL_SECONDS,
            }
