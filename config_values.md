# Config Table Values

Enter these rows in the `00a Config` sheet:

| config_key | config_value | effective_from | changed_by |
|------------|--------------|----------------|------------|
| `min_remnant_area_m2` | `0.5` | 2026-01-07 | admin |
| `t1_cutoff_time_local` | `18:00` | 2026-01-07 | admin |
| `t1_cutoff_timezone` | `Asia/Dubai` | 2026-01-07 | admin |
| `allocation_expiry_minutes` | `720` | 2026-01-07 | admin |
| `variance_tolerance_pct` | `2.0` | 2026-01-07 | admin |
| `consumption_tolerance_pct` | `5.0` | 2026-01-07 | admin |
| `remnant_value_fraction` | `0.7` | 2026-01-07 | admin |
| `parser_version_current` | `1.0.0` | 2026-01-07 | admin |
| `vacuum_bed_length_mm` | `6000` | 2026-01-07 | admin |
| `vacuum_bed_width_mm` | `3200` | 2026-01-07 | admin |
| `truck_capacity_10ton_m2` | `180` | 2026-01-07 | admin |
| `truck_capacity_3ton_m2` | `60` | 2026-01-07 | admin |
| `shift_morning_start` | `07:00` | 2026-01-07 | admin |
| `shift_morning_end` | `15:00` | 2026-01-07 | admin |
| `shift_evening_start` | `15:00` | 2026-01-07 | admin |
| `shift_evening_end` | `23:00` | 2026-01-07 | admin |
| `sla_exception_critical_hours` | `4` | 2026-01-07 | admin |
| `sla_exception_high_hours` | `24` | 2026-01-07 | admin |
| `approval_required_min_value_aed` | `50000` | 2026-01-07 | admin |

---

## Config Key Descriptions

| Key | Purpose |
|-----|---------|
| `min_remnant_area_m2` | Minimum area for offcut to be logged as reusable remnant |
| `t1_cutoff_time_local` | Daily deadline for T-1 nesting completion |
| `allocation_expiry_minutes` | How long allocations stay reserved (12 hours default) |
| `variance_tolerance_pct` | Max allowed variance before exception is created |
| `consumption_tolerance_pct` | Allowed overconsumption before blocking DO |
| `remnant_value_fraction` | Value multiplier for remnant inventory valuation |
| `parser_version_current` | Current nesting file parser version |
| `vacuum_bed_*` | Machine dimensions for remnant usability check |
| `truck_capacity_*` | Used by load builder for dispatch optimization |
| `shift_*` | Shift time boundaries for allocation assignment |
| `sla_exception_*` | Exception escalation timeframes by severity |
| `approval_required_min_value_aed` | Threshold for requiring manager approval |
