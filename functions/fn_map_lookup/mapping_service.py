"""
Mapping Service Core Logic
==========================

Provides deterministic material mapping from nesting descriptions to canonical codes.

Lookup Order (Precedence):
1. Check idempotency (Mapping History)
2. Exact match in Material Master (05a) → canonical_code + default_sap_code
3. Override check (05b) by scope: LPO > BRAND > PROJECT > CUSTOMER
4. Resolve SAP code (override or default)
5. Look up SAP Material Catalog (05c) for UOM + conversion factor
6. If no match → create exception

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
    """Cached entry from Material Master (05a) — identity + default SAP code."""
    
    row_id: int
    nesting_description: str
    canonical_code: str
    default_sap_code: Optional[str] = None
    sap_description: Optional[str] = None
    not_tracked: bool = False
    active: bool = True


@dataclass
class CatalogEntry:
    """Cached entry from SAP Material Catalog (05c) — conversion factors."""
    
    row_id: int
    sap_code: str
    canonical_code: str
    nesting_description: str = ""
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
        result = service.lookup("aluminum tape", brand="WTI", lpo_id="LPO-555", trace_id="abc123")
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
        
        # 05a Material Master cache: normalized_description → MaterialMasterEntry
        self._material_master_cache: Dict[str, MaterialMasterEntry] = {}
        self._cache_timestamp: Optional[datetime] = None
        
        # 05c SAP Catalog cache: sap_code → CatalogEntry
        self._catalog_cache: Dict[str, CatalogEntry] = {}
        self._catalog_cache_timestamp: Optional[datetime] = None
        
        # 05b Override cache
        self._override_cache: List[Dict] = []
        self._override_cache_timestamp: Optional[datetime] = None
        
        self._cache_lock = threading.Lock()
        self._initialized = True
        
        logger.info("MappingService initialized")
    
    def lookup(
        self,
        nesting_description: str,
        brand: Optional[str] = None,
        lpo_id: Optional[str] = None,
        project_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        ingest_line_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> MappingResult:
        """
        Look up canonical mapping for a nesting description.
        
        Lookup order:
        1. Idempotency check (Mapping History)
        2. Material Master (05a) → canonical_code + default_sap_code
        3. Override table (05b) by scope: LPO > BRAND > PROJECT > CUSTOMER
        4. Resolve SAP code → override wins, else default from 05a
        5. SAP Material Catalog (05c) → UOM + conversion factor
        6. No match in 05a → create exception
        
        Args:
            nesting_description: Raw description from nesting file
            brand: Brand name (e.g., "WTI", "KIMMCO") for override lookup
            lpo_id: Optional LPO ID for override lookup
            project_id: Optional project ID for override lookup
            customer_id: Optional customer ID for override lookup
            ingest_line_id: Unique ID for this lookup (for history)
            trace_id: Trace ID for distributed tracing
            
        Returns:
            MappingResult with canonical code, SAP code, and conversion factors
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
            # ── Step 1: Material Master (05a) → identity + default ──────
            entry = self._lookup_material_master(normalized)
            if not entry:
                # No match in Material Master → exception
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
                return result
            
            # We have identity
            result.success = True
            result.canonical_code = entry.canonical_code
            result.not_tracked = entry.not_tracked
            resolved_sap_code = entry.default_sap_code
            result.decision = "AUTO"
            
            # ── Step 2: Override (05b) → check for brand/LPO override ──
            if brand or lpo_id or project_id or customer_id:
                override_result = self._check_overrides(
                    normalized, brand, lpo_id, project_id, customer_id
                )
                if override_result:
                    resolved_sap_code = override_result.get("sap_code") or resolved_sap_code
                    # Override may also provide a different canonical code
                    if override_result.get("canonical_code"):
                        result.canonical_code = override_result["canonical_code"]
                    result.decision = "OVERRIDE"
                    logger.info(
                        f"[{trace_id}] Override applied: SAP code = {resolved_sap_code}"
                    )
            
            result.sap_code = resolved_sap_code
            
            # ── Step 3: SAP Catalog (05c) → conversion factors ─────────
            if resolved_sap_code:
                catalog_entry = self._lookup_catalog(resolved_sap_code)
                if catalog_entry:
                    result.uom = catalog_entry.uom
                    result.sap_uom = catalog_entry.sap_uom
                    result.conversion_factor = catalog_entry.conversion_factor
                    if catalog_entry.not_tracked:
                        result.not_tracked = True
                    logger.debug(
                        f"[{trace_id}] Catalog hit for SAP {resolved_sap_code}: "
                        f"uom={catalog_entry.uom}, factor={catalog_entry.conversion_factor}"
                    )
                else:
                    logger.warning(
                        f"[{trace_id}] SAP code {resolved_sap_code} not found in "
                        f"SAP Material Catalog (05c)"
                    )
            
            # ── Step 4: Log history ────────────────────────────────────
            result.history_id = self._log_history(
                ingest_line_id, normalized, result, trace_id
            )
            
        except Exception as e:
            logger.exception(f"[{trace_id}] Error during mapping lookup: {e}")
            result.error = str(e)
        
        return result

    def get_sap_conflicts(self) -> Dict[str, List[CatalogEntry]]:
        """Find canonical codes with multiple SAP codes in catalog (05c).

        Returns dict of canonical_code -> list of CatalogEntry (only where len >= 2).
        """
        self._ensure_catalog_cache_fresh()
        groups: Dict[str, List[CatalogEntry]] = {}
        with self._cache_lock:
            for entry in self._catalog_cache.values():
                if entry.canonical_code:
                    groups.setdefault(entry.canonical_code, []).append(entry)
        return {k: v for k, v in groups.items() if len(v) >= 2}

    def get_material_description(self, canonical_code: str) -> Optional[str]:
        """Get SAP description from Material Master (05a) by canonical code."""
        self._ensure_cache_fresh()
        with self._cache_lock:
            for entry in self._material_master_cache.values():
                if entry.canonical_code == canonical_code:
                    return entry.sap_description
        return None

    def get_default_sap_code(self, canonical_code: str) -> Optional[str]:
        """Get default SAP code from Material Master (05a) by canonical code."""
        self._ensure_cache_fresh()
        with self._cache_lock:
            for entry in self._material_master_cache.values():
                if entry.canonical_code == canonical_code:
                    return entry.default_sap_code
        return None

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
    
    # ── Material Master (05a) cache ─────────────────────────────────────
    
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
                
                entry = MaterialMasterEntry(
                    row_id=row["id"],
                    nesting_description=normalized,
                    canonical_code=str(cells.get(col_ids["CANONICAL_CODE"], "")),
                    default_sap_code=cells.get(col_ids.get("DEFAULT_SAP_CODE")),
                    sap_description=cells.get(col_ids.get("SAP_DESCRIPTION")),
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
        return manifest.get_all_column_ids("MATERIAL_MASTER")
    
    # ── SAP Material Catalog (05c) cache ────────────────────────────────
    
    def _lookup_catalog(self, sap_code: str) -> Optional[CatalogEntry]:
        """
        Look up SAP code in SAP Material Catalog cache.
        
        Returns conversion factor and UOM details for the given SAP code.
        """
        self._ensure_catalog_cache_fresh()
        
        with self._cache_lock:
            return self._catalog_cache.get(str(sap_code))
    
    def _ensure_catalog_cache_fresh(self) -> None:
        """Refresh SAP Catalog cache if stale."""
        now = datetime.utcnow()
        
        if self._catalog_cache_timestamp:
            age = (now - self._catalog_cache_timestamp).total_seconds()
            if age < self.CACHE_TTL_SECONDS:
                return
        
        self._refresh_catalog_cache()
    
    def _refresh_catalog_cache(self) -> None:
        """Reload SAP Material Catalog from Smartsheet."""
        logger.info("Refreshing SAP Material Catalog cache...")
        
        try:
            from shared.logical_names import Sheet
            
            rows = self._client.get_all_rows(Sheet.SAP_MATERIAL_CATALOG)
            col_ids = self._get_catalog_column_ids()
            
            new_cache: Dict[str, CatalogEntry] = {}
            
            for row in rows:
                cells = {c.get("columnId"): c.get("value") for c in row.get("cells", [])}
                
                sap_code = cells.get(col_ids.get("SAP_CODE"))
                if not sap_code:
                    continue
                
                sap_code = str(sap_code)
                
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
                        logger.warning(f"Invalid conversion_factor '{conv_factor_val}' for SAP code {sap_code}, treating as None")
                        conv_factor = None
                
                entry = CatalogEntry(
                    row_id=row["id"],
                    sap_code=sap_code,
                    canonical_code=str(cells.get(col_ids.get("CANONICAL_CODE"), "")),
                    nesting_description=str(cells.get(col_ids.get("NESTING_DESCRIPTION"), "")),
                    uom=cells.get(col_ids.get("UOM")),
                    sap_uom=cells.get(col_ids.get("SAP_UOM")),
                    conversion_factor=conv_factor,
                    not_tracked=not_tracked,
                    active=True,
                )
                
                new_cache[sap_code] = entry
            
            with self._cache_lock:
                self._catalog_cache = new_cache
                self._catalog_cache_timestamp = datetime.utcnow()
            
            logger.info(f"SAP Material Catalog cache refreshed: {len(new_cache)} entries")
            
        except Exception as e:
            logger.exception(f"Error refreshing SAP Material Catalog cache: {e}")
            raise
    
    def _get_catalog_column_ids(self) -> Dict[str, int]:
        """Get column IDs for SAP Material Catalog from manifest."""
        from shared.manifest import get_manifest
        
        manifest = get_manifest()
        return manifest.get_all_column_ids("05C_SAP_MATERIAL_CATALOG")
    
    # ── Override (05b) cache ────────────────────────────────────────────
    
    def _check_overrides(
        self,
        normalized_description: str,
        brand: Optional[str],
        lpo_id: Optional[str],
        project_id: Optional[str],
        customer_id: Optional[str],
    ) -> Optional[Dict[str, str]]:
        """
        Check override table with scope precedence.
        
        Precedence: LPO > BRAND > PROJECT > CUSTOMER
        """
        from shared.logical_names import Sheet
        
        # Build scope checks in precedence order
        scope_checks = []
        if lpo_id:
            scope_checks.append(("LPO", lpo_id))
        if brand:
            scope_checks.append(("BRAND", brand))
        if project_id:
            scope_checks.append(("PROJECT", project_id))
        if customer_id:
            scope_checks.append(("CUSTOMER", customer_id))
        
        if not scope_checks:
            return None
        
        try:
            # Use cached override data to prevent N+1 API calls
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
                    
                    # Check effective dates (use UTC to match cache timestamps)
                    from shared.helpers import now_uae
                    now_date = now_uae().date()
                    
                    eff_from_str = str(cells.get(col_ids.get("EFFECTIVE_FROM"), "")).split("T")[0]
                    eff_to_str = str(cells.get(col_ids.get("EFFECTIVE_TO"), "")).split("T")[0]
                    
                    if eff_from_str:
                        try:
                            eff_from = datetime.strptime(eff_from_str, "%Y-%m-%d").date()
                            if now_date < eff_from:
                                continue
                        except ValueError:
                            pass  # Ignore invalid dates
                            
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
            # Serve stale cache if available, but reset timestamp to avoid
            # hammering the API on every subsequent call
            if self._override_cache:
                self._override_cache_timestamp = now
                return self._override_cache
            self._override_cache = []
        
        return self._override_cache
    
    def _get_override_column_ids(self) -> Dict[str, int]:
        """Get column IDs for Mapping Override from manifest."""
        from shared.manifest import get_manifest
        
        manifest = get_manifest()
        return manifest.get_all_column_ids("MAPPING_OVERRIDE")
    
    # ── History + Exception logging ─────────────────────────────────────
    
    def _check_existing_history(self, ingest_line_id: str, trace_id: str) -> Optional[MappingResult]:
        """
        Check if an entry already exists for this ingest line.
        Returns MappingResult if found.
        """
        from shared.logical_names import Sheet, Column
        
        try:
            row = self._client.find_row(
                Sheet.MAPPING_HISTORY, 
                Column.MAPPING_HISTORY.INGEST_LINE_ID, 
                ingest_line_id
            )
            
            if row:
                # COMPAT: Multiple aliases handle pre-manifest history rows with varying column names
                canonical = row.get("Canonical Code") or row.get("CanonicalCode") or ""
                sap = row.get("SAP Code") or row.get("SAPCode") or ""
                decision = row.get("Decision") or "AUTO"

                # COMPAT: Try both column name variants for conversion context
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
        from shared.helpers import now_uae, format_datetime_for_smartsheet

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
                col_ids["CREATED_AT"]: format_datetime_for_smartsheet(now_uae()),
                col_ids["NOTES"]: result.error or "",
                
                # Persist conversion context if columns exist
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
        return manifest.get_all_column_ids("MAPPING_HISTORY")
    
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
        from shared.helpers import now_uae, format_datetime_for_smartsheet

        exception_id = f"MAPEX-{str(uuid4())[:8]}"
        
        try:
            col_ids = self._get_exception_column_ids()
            
            row_data = {
                col_ids["EXCEPTION_ID"]: exception_id,
                col_ids["INGEST_LINE_ID"]: ingest_line_id,
                col_ids["NESTING_DESCRIPTION"]: nesting_description,
                col_ids["STATUS"]: "OPEN",
                col_ids["CREATED_AT"]: format_datetime_for_smartsheet(now_uae()),
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
        return manifest.get_all_column_ids("MAPPING_EXCEPTION")
    
    # ── Cache management ────────────────────────────────────────────────
    
    def invalidate_cache(self) -> None:
        """Force cache refresh on next lookup."""
        with self._cache_lock:
            self._cache_timestamp = None
            self._catalog_cache_timestamp = None
            self._override_cache_timestamp = None
        logger.info("All mapping caches invalidated")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics for monitoring."""
        with self._cache_lock:
            master_age = None
            if self._cache_timestamp:
                master_age = (datetime.utcnow() - self._cache_timestamp).total_seconds()
            
            catalog_age = None
            if self._catalog_cache_timestamp:
                catalog_age = (datetime.utcnow() - self._catalog_cache_timestamp).total_seconds()
            
            return {
                "material_master_entries": len(self._material_master_cache),
                "catalog_entries": len(self._catalog_cache),
                "override_entries": len(self._override_cache),
                "master_cache_age_seconds": master_age,
                "catalog_cache_age_seconds": catalog_age,
                "ttl_seconds": self.CACHE_TTL_SECONDS,
                "is_stale": master_age is None or master_age >= self.CACHE_TTL_SECONDS,
            }
