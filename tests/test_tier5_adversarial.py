"""
tests/test_tier5_adversarial.py - Consolidated Master Tier 5 Adversarial Test Suite.

Consolidates all 70 Tier 5 adversarial stress tests across:
- Part 1: Hardware, Virtual ESP32 FSM, MQTT Ingestion & Backend Core (30 tests)
          authored in tests/test_tier5_adversarial_p1.py
- Part 2: SteVe OCPP 1.6-J CSMS & LF Energy EVerest Bridge Deep Hardening (40 tests)
          authored in tests/test_tier5_adversarial_p2.py

Usage:
  - Run all 70 Tier 5 tests:
      pytest tests/test_tier5_adversarial.py -v
  - Run Part 1 tests (30 tests):
      pytest tests/test_tier5_adversarial_p1.py -v
  - Run Part 2 tests (40 tests):
      pytest tests/test_tier5_adversarial_p2.py -v
  - Run full repository suite (386 tests):
      pytest tests/ -v
"""

import os
import sys

# Determine if this consolidated suite is specifically targeted in invocation.
# When pytest runs on the `tests/` directory (e.g. `pytest tests/` or `pytest`),
# pytest automatically discovers and executes tests from `test_tier5_adversarial_p1.py` (30)
# and `test_tier5_adversarial_p2.py` (40), totaling 70 Tier 5 tests (386 across repo).
# When this master suite is explicitly invoked (e.g. `pytest tests/test_tier5_adversarial.py`),
# this file exports all 70 tests directly.
def _is_master_target() -> bool:
    if os.getenv("TIER5_FORCE_EXPORT") == "1":
        return True
    has_master = False
    has_part = False
    for arg in sys.argv:
        norm = arg.replace("\\", "/")
        if "_p1" in norm or "_p2" in norm:
            has_part = True
        elif "test_tier5_adversarial" in norm:
            has_master = True
    return has_master and not has_part


if _is_master_target():
    # Import Part 1: Hardware, Virtual ESP32 FSM, MQTT Ingestion & Backend Core (30 tests)
    from tests.test_tier5_adversarial_p1 import (
        test_adv_fsm_reject_reserve_when_faulted,
        test_adv_fsm_reject_unauthenticated_start_charging,
        test_adv_fsm_negative_tick_delta_exception_guard,
        test_adv_fsm_out_of_order_auth_decision_while_available,
        test_adv_fsm_rapid_keypad_burst_during_countdown,
        test_adv_fsm_telemetry_extreme_adc_saturation_and_clamping,
        test_adv_fsm_microsd_ring_buffer_saturation_and_fifo_eviction,
        test_adv_fsm_microsd_csv_row_column_count_and_sanitization,
        test_adv_fsm_zero_current_detection_clean_session_termination,
        test_adv_fsm_simulated_brownout_reboot_preserves_safety,
        test_adv_mqtt_malformed_json_bytes_resilience,
        test_adv_mqtt_non_dict_payload_ignored,
        test_adv_mqtt_telemetry_nan_inf_none_coercion,
        test_adv_mqtt_topic_wildcard_adversarial_patterns,
        test_adv_mqtt_custom_listener_exception_isolation,
        test_adv_mqtt_publish_offline_buffering,
        test_adv_mqtt_auth_expired_reservation_immediate_denial,
        test_adv_mqtt_auth_whitespace_and_case_normalization,
        test_adv_db_concurrent_reservations_race_condition,
        test_adv_db_get_reservation_nonexistent_and_wildcard,
        test_adv_db_sql_injection_defense_in_parameters,
        test_adv_db_telemetry_high_concurrency_batch_inserts,
        test_adv_db_rfid_authorization_inactive_and_expired_states,
        test_adv_db_delete_session_and_reservation_idempotency,
        test_adv_api_reserve_conflict_when_connector_charging,
        test_adv_api_reserve_conflict_when_connector_faulted,
        test_adv_api_reserve_renewal_same_user_vs_different_user,
        test_adv_api_verify_otp_expired_returns_410,
        test_adv_api_cancel_reservation_unauthorized_user_forbidden,
        test_adv_api_reserve_invalid_station_404,
    )

    # Import Part 2: SteVe OCPP 1.6-J CSMS & LF Energy EVerest Bridge Deep Hardening (40 tests)
    from tests.test_tier5_adversarial_p2 import (
        TestOCPPFramingAdversarial,
        TestVirtualCSMSProtocolEdgeCases,
        TestBridgeTransactionLifecycleEdgeCases,
        TestBridgeConcurrencyAndEVerestTaxonomy,
    )

    __all__ = [
        "test_adv_fsm_reject_reserve_when_faulted",
        "test_adv_fsm_reject_unauthenticated_start_charging",
        "test_adv_fsm_negative_tick_delta_exception_guard",
        "test_adv_fsm_out_of_order_auth_decision_while_available",
        "test_adv_fsm_rapid_keypad_burst_during_countdown",
        "test_adv_fsm_telemetry_extreme_adc_saturation_and_clamping",
        "test_adv_fsm_microsd_ring_buffer_saturation_and_fifo_eviction",
        "test_adv_fsm_microsd_csv_row_column_count_and_sanitization",
        "test_adv_fsm_zero_current_detection_clean_session_termination",
        "test_adv_fsm_simulated_brownout_reboot_preserves_safety",
        "test_adv_mqtt_malformed_json_bytes_resilience",
        "test_adv_mqtt_non_dict_payload_ignored",
        "test_adv_mqtt_telemetry_nan_inf_none_coercion",
        "test_adv_mqtt_topic_wildcard_adversarial_patterns",
        "test_adv_mqtt_custom_listener_exception_isolation",
        "test_adv_mqtt_publish_offline_buffering",
        "test_adv_mqtt_auth_expired_reservation_immediate_denial",
        "test_adv_mqtt_auth_whitespace_and_case_normalization",
        "test_adv_db_concurrent_reservations_race_condition",
        "test_adv_db_get_reservation_nonexistent_and_wildcard",
        "test_adv_db_sql_injection_defense_in_parameters",
        "test_adv_db_telemetry_high_concurrency_batch_inserts",
        "test_adv_db_rfid_authorization_inactive_and_expired_states",
        "test_adv_db_delete_session_and_reservation_idempotency",
        "test_adv_api_reserve_conflict_when_connector_charging",
        "test_adv_api_reserve_conflict_when_connector_faulted",
        "test_adv_api_reserve_renewal_same_user_vs_different_user",
        "test_adv_api_verify_otp_expired_returns_410",
        "test_adv_api_cancel_reservation_unauthorized_user_forbidden",
        "test_adv_api_reserve_invalid_station_404",
        "TestOCPPFramingAdversarial",
        "TestVirtualCSMSProtocolEdgeCases",
        "TestBridgeTransactionLifecycleEdgeCases",
        "TestBridgeConcurrencyAndEVerestTaxonomy",
    ]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main(["-v", __file__]))
