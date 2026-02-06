"""
Sequence-based ID Generator

Generates human-readable, sequential IDs stored in the Config sheet.
Format: PREFIX-NNNN (e.g., TAG-0001, EX-0042, ALLOC-0123)

This approach is:
- Human-friendly and easy to communicate
- Immutable (IDs are never reused)
- Migration-ready (same pattern works with SQL sequences)
- Centralized (no collisions across functions)

Concurrency handling:
- Uses retry with collision detection since Smartsheet doesn't support atomic ops
- Exponential backoff on retry to reduce contention
"""

import logging
import time
import random
from typing import Optional
from datetime import datetime

from .sheet_config import ConfigKey, ID_PREFIXES, SheetName, ColumnName

logger = logging.getLogger(__name__)


class SequenceCollisionError(Exception):
    """Raised when a sequence collision is detected and max retries exceeded."""
    pass


class SequenceGenerator:
    """
    Generates sequential IDs using the Config sheet as persistent storage.
    
    Implements retry logic to handle race conditions since Smartsheet
    doesn't support atomic compare-and-swap operations.
    
    Usage:
        from shared import SequenceGenerator, get_smartsheet_client
        
        client = get_smartsheet_client()
        gen = SequenceGenerator(client)
        
        tag_id = gen.next_id(ConfigKey.SEQ_TAG)  # Returns "TAG-0001"
    """
    
    # Retry configuration
    MAX_RETRIES = 5
    BASE_DELAY_MS = 50  # Starting delay in milliseconds
    MAX_DELAY_MS = 2000  # Maximum delay in milliseconds
    
    def __init__(self, smartsheet_client):
        """
        Initialize with a Smartsheet client.
        
        Args:
            smartsheet_client: Instance of SmartsheetClient
        """
        self.client = smartsheet_client
        self._cache: dict = {}  # Cache for config row IDs and values
    
    def next_id(self, sequence_key: ConfigKey, padding: int = 4) -> str:
        """
        Get the next sequential ID for a given sequence.
        
        Uses retry with collision detection to handle concurrent access.
        Each retry reads fresh data to detect if another process updated
        the sequence.
        
        Args:
            sequence_key: ConfigKey enum for the sequence (e.g., ConfigKey.SEQ_TAG)
            padding: Number of digits to zero-pad (default: 4)
        
        Returns:
            Formatted ID string (e.g., "TAG-0001")
        
        Raises:
            SequenceCollisionError: If max retries exceeded due to contention
        """
        last_error = None
        
        for attempt in range(self.MAX_RETRIES):
            try:
                # Clear cache to get fresh value on retries
                if attempt > 0:
                    self._cache.pop(sequence_key, None)
                
                # Get current value (fresh read)
                current = self._get_sequence_value(sequence_key)
                expected_row_id = self._cache.get(sequence_key)
                
                # Calculate next value
                next_val = current + 1
                
                # Attempt to write back
                success = self._try_update_sequence(sequence_key, next_val, expected_row_id)
                
                if success:
                    # Format and return ID
                    prefix = ID_PREFIXES.get(sequence_key, "ID")
                    generated_id = f"{prefix}-{next_val:0{padding}d}"
                    logger.debug(f"Generated ID: {generated_id} (attempt {attempt + 1})")
                    return generated_id
                
                # Collision detected - retry
                logger.warning(f"Sequence collision for {sequence_key.value}, attempt {attempt + 1}/{self.MAX_RETRIES}")
                
            except Exception as e:
                last_error = e
                logger.warning(f"Error generating ID for {sequence_key.value}: {e}, attempt {attempt + 1}/{self.MAX_RETRIES}")
            
            # Exponential backoff with jitter
            if attempt < self.MAX_RETRIES - 1:
                delay_ms = min(
                    self.BASE_DELAY_MS * (2 ** attempt) + random.randint(0, 50),
                    self.MAX_DELAY_MS
                )
                time.sleep(delay_ms / 1000.0)
        
        # All retries exhausted
        error_msg = f"Failed to generate ID for {sequence_key.value} after {self.MAX_RETRIES} attempts"
        logger.error(error_msg)
        raise SequenceCollisionError(error_msg) from last_error
    
    def _get_sequence_value(self, sequence_key: ConfigKey) -> int:
        """Get current sequence value from Config sheet (fresh read)."""
        row = self.client.find_row_by_column(
            SheetName.CONFIG.value,
            ColumnName.CONFIG_KEY,
            sequence_key.value
        )
        
        if row:
            self._cache[sequence_key] = row.get("row_id")
            value = row.get(ColumnName.CONFIG_VALUE)
            try:
                return int(value) if value else 0
            except (ValueError, TypeError):
                return 0
        
        # Sequence doesn't exist, create it
        logger.info(f"Creating new sequence: {sequence_key.value}")
        self._create_sequence(sequence_key)
        return 0
    
    def _try_update_sequence(self, sequence_key: ConfigKey, new_value: int, expected_row_id: Optional[int]) -> bool:
        """
        Attempt to update the sequence value.
        
        Returns True if update succeeded, False if a collision was detected.
        Note: Smartsheet doesn't support true optimistic locking, so we can't
        fully prevent race conditions. This at least catches obvious collisions.
        """
        try:
            if expected_row_id:
                self.client.update_row(
                    SheetName.CONFIG.value,
                    expected_row_id,
                    {
                        ColumnName.CONFIG_VALUE: str(new_value),
                        ColumnName.CHANGED_BY: "system"
                    }
                )
                return True
            else:
                # Row doesn't exist in cache, need to find it
                row = self.client.find_row_by_column(
                    SheetName.CONFIG.value,
                    ColumnName.CONFIG_KEY,
                    sequence_key.value
                )
                if row:
                    self.client.update_row(
                        SheetName.CONFIG.value,
                        row.get("row_id"),
                        {
                            ColumnName.CONFIG_VALUE: str(new_value),
                            ColumnName.CHANGED_BY: "system"
                        }
                    )
                    return True
                else:
                    # Create the row with the new value
                    self._create_sequence(sequence_key, new_value)
                    return True
                    
        except Exception as e:
            # Check if this was a collision (4004 error)
            error_str = str(e).lower()
            if "4004" in error_str or "collision" in error_str or "conflict" in error_str:
                return False
            # Re-raise other errors
            raise
    
    def _create_sequence(self, sequence_key: ConfigKey, initial_value: int = 0):
        """Create a new sequence row in Config sheet."""
        row_data = {
            ColumnName.CONFIG_KEY: sequence_key.value,
            ColumnName.CONFIG_VALUE: str(initial_value),
            ColumnName.EFFECTIVE_FROM: datetime.now().strftime("%Y-%m-%d"),
            ColumnName.CHANGED_BY: "system"
        }
        result = self.client.add_row(SheetName.CONFIG.value, row_data)
        self._cache[sequence_key] = result.get("id")
    
    def peek_next(self, sequence_key: ConfigKey, padding: int = 4) -> str:
        """
        Preview what the next ID would be without incrementing.
        Useful for display purposes before committing.
        
        Note: This is not guaranteed to be the actual next ID if there's
        concurrent access.
        
        Args:
            sequence_key: ConfigKey enum for the sequence
            padding: Number of digits to zero-pad
        
        Returns:
            What the next ID would be (approximate)
        """
        current = self._get_sequence_value(sequence_key)
        prefix = ID_PREFIXES.get(sequence_key, "ID")
        return f"{prefix}-{current + 1:0{padding}d}"
    
    def current_value(self, sequence_key: ConfigKey) -> int:
        """Get the current sequence value without incrementing."""
        return self._get_sequence_value(sequence_key)


# ============== Convenience Functions ==============
# These require a client to be passed in for thread safety

def generate_next_tag_id(client) -> str:
    """Generate next Tag ID (e.g., TAG-0001)."""
    return SequenceGenerator(client).next_id(ConfigKey.SEQ_TAG)


def generate_next_lpo_id(client) -> str:
    """Generate next LPO ID (e.g., LPO-0001). v1.6.8"""
    return SequenceGenerator(client).next_id(ConfigKey.SEQ_LPO)


def generate_next_exception_id(client) -> str:
    """Generate next Exception ID (e.g., EX-0001)."""
    return SequenceGenerator(client).next_id(ConfigKey.SEQ_EXCEPTION)


def generate_next_allocation_id(client) -> str:
    """Generate next Allocation ID (e.g., ALLOC-0001)."""
    return SequenceGenerator(client).next_id(ConfigKey.SEQ_ALLOCATION)


def generate_next_consumption_id(client) -> str:
    """Generate next Consumption ID (e.g., CON-0001)."""
    return SequenceGenerator(client).next_id(ConfigKey.SEQ_CONSUMPTION)


def generate_next_delivery_id(client) -> str:
    """Generate next Delivery ID (e.g., DO-0001)."""
    return SequenceGenerator(client).next_id(ConfigKey.SEQ_DELIVERY)


def generate_next_nesting_id(client) -> str:
    """Generate next Nesting Session ID (e.g., NEST-0001)."""
    return SequenceGenerator(client).next_id(ConfigKey.SEQ_NESTING)


def generate_next_remnant_id(client) -> str:
    """Generate next Remnant ID (e.g., REM-0001)."""
    return SequenceGenerator(client).next_id(ConfigKey.SEQ_REMNANT)


def generate_next_filler_id(client) -> str:
    """Generate next Filler ID (e.g., FILL-0001)."""
    return SequenceGenerator(client).next_id(ConfigKey.SEQ_FILLER)


def generate_next_txn_id(client) -> str:
    """Generate next Transaction ID (e.g., TXN-0001)."""
    return SequenceGenerator(client).next_id(ConfigKey.SEQ_TXN)


def generate_next_action_id(client) -> str:
    """Generate next User Action ID (e.g., ACT-0001)."""
    return SequenceGenerator(client).next_id(ConfigKey.SEQ_ACTION)


def generate_next_schedule_id(client) -> str:
    """Generate next Production Schedule ID (e.g., SCHED-0001)."""
    return SequenceGenerator(client).next_id(ConfigKey.SEQ_SCHEDULE)


