import pytest
from gltest import *


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("contracts/circuit_breaker_x.py")


def test_initial_state(contract):
    assert contract.get_report_count() == 0
    assert contract.get_min_stake() == 100
    stats = contract.get_contract_stats()
    assert '"min_stake": "100"' in stats
    assert '"report_count": "0"' in stats
    assert '"treasury_balance": "0"' in stats


def test_register_and_fund_protocol(contract, direct_vm, direct_alice, direct_bob):
    protocol_addr = direct_alice
    direct_vm.sender = protocol_addr
    direct_vm.value = 5000  # Initial emergency bounty pool deposit

    contract.register_target_protocol(protocol_addr)

    status_json = contract.get_protocol_status(protocol_addr)
    assert '"is_registered": true' in status_json
    assert '"is_halted": false' in status_json
    assert '"bounty_pool": "5000"' in status_json
    assert contract.is_halted(protocol_addr) is False

    # Top-up bounty pool
    direct_vm.sender = direct_bob
    direct_vm.value = 2500
    contract.deposit_protocol_bounty(protocol_addr)

    status_json_after = contract.get_protocol_status(protocol_addr)
    assert '"bounty_pool": "7500"' in status_json_after


def test_submit_exploit_report_success(contract, direct_vm, direct_alice, direct_bob):
    protocol_addr = direct_alice
    whitehat = direct_bob

    direct_vm.sender = protocol_addr
    direct_vm.value = 1000
    contract.register_target_protocol(protocol_addr)

    # Whitehat stakes 200 GEN (>= min_stake 100) and submits report
    direct_vm.sender = whitehat
    direct_vm.value = 200
    report_id = contract.submit_exploit_report(
        protocol_addr,
        "https://security.example.com/incident_report.txt"
    )

    assert str(report_id) == "1"
    assert contract.get_report_count() == 1

    report_json = contract.get_report(report_id)
    assert '"report_id": "1"' in report_json
    assert '"status": "PENDING"' in report_json
    assert '"staked_amount": "200"' in report_json


def test_submit_report_fails_insufficient_stake(contract, direct_vm, direct_alice, direct_bob):
    protocol_addr = direct_alice
    whitehat = direct_bob

    direct_vm.sender = whitehat
    direct_vm.value = 50  # Below 100 GEN min_stake
    with pytest.raises(Exception):
        contract.submit_exploit_report(
            protocol_addr,
            "https://security.example.com/exploit.txt"
        )


def test_adjudicate_confirmed_exploit_triggers_halt(contract, direct_vm, direct_alice, direct_bob):
    protocol_addr = direct_alice
    whitehat = direct_bob

    # Protocol registers with 10,000 GEN bounty pool
    direct_vm.sender = protocol_addr
    direct_vm.value = 10000
    contract.register_target_protocol(protocol_addr)

    # Whitehat submits report
    direct_vm.sender = whitehat
    direct_vm.value = 300
    report_id = contract.submit_exploit_report(
        protocol_addr,
        "https://peckshield.example.com/exploit_alert.txt"
    )

    # Install Mocks for Nondet consensus
    direct_vm.mock_web("exploit_alert.txt", {
        "status": 200,
        "body": "CRITICAL ALERT: Protocol reentrancy exploit detected. $5M drained to attacker 0xabc."
    })
    direct_vm.mock_llm(".*", '{"verdict": "CONFIRMED_EXPLOIT", "confidence": 95, "severity": "CRITICAL", "reason": "Verified reentrancy exploit with drained funds."}')

    contract.adjudicate_report(report_id)

    # Protocol must be in EMERGENCY HALT state!
    assert contract.is_halted(protocol_addr) is True

    report_json = contract.get_report(report_id)
    assert '"status": "CONFIRMED"' in report_json
    assert '"verdict": "CONFIRMED_EXPLOIT"' in report_json

    protocol_status = contract.get_protocol_status(protocol_addr)
    assert '"is_halted": true' in protocol_status
    # Bounty was paid out to whitehat
    assert '"bounty_pool": "0"' in protocol_status


def test_adjudicate_false_alarm_slashes_stake(contract, direct_vm, direct_alice, direct_bob):
    protocol_addr = direct_alice
    reporter = direct_bob

    direct_vm.sender = protocol_addr
    direct_vm.value = 5000
    contract.register_target_protocol(protocol_addr)

    # Reporter stakes 150 GEN
    direct_vm.sender = reporter
    direct_vm.value = 150
    report_id = contract.submit_exploit_report(
        protocol_addr,
        "https://news.example.com/crypto_rumor.txt"
    )

    # Install Mocks returning FALSE_ALARM
    direct_vm.mock_web("crypto_rumor.txt", {
        "status": 200,
        "body": "Market drops 2% today amid routine trading volatility. No vulnerabilities found."
    })
    direct_vm.mock_llm(".*", '{"verdict": "FALSE_ALARM", "confidence": 90, "severity": "NONE", "reason": "Routine market volatility, no exploit or code compromise."}')

    contract.adjudicate_report(report_id)

    # Protocol must NOT be halted
    assert contract.is_halted(protocol_addr) is False

    report_json = contract.get_report(report_id)
    assert '"status": "DISMISSED"' in report_json
    assert '"verdict": "FALSE_ALARM"' in report_json

    # Slashed stake moved into treasury balance
    stats = contract.get_contract_stats()
    assert '"treasury_balance": "150"' in stats


def test_resume_protocol_after_patch(contract, direct_vm, direct_alice, direct_bob, direct_owner):
    protocol_addr = direct_alice
    whitehat = direct_bob
    owner = direct_owner

    direct_vm.sender = protocol_addr
    direct_vm.value = 2000
    contract.register_target_protocol(protocol_addr)

    direct_vm.sender = whitehat
    direct_vm.value = 100
    report_id = contract.submit_exploit_report(
        protocol_addr,
        "https://security.example.com/exploit.txt"
    )

    direct_vm.mock_web("exploit.txt", {"status": 200, "body": "EMERGENCY: Reentrancy vulnerability actively exploited, funds drained."})
    direct_vm.mock_llm(".*", '{"verdict": "CONFIRMED_EXPLOIT", "confidence": 98, "severity": "CRITICAL", "reason": "Exploit confirmed."}')
    contract.adjudicate_report(report_id)

    assert contract.is_halted(protocol_addr) is True

    # Unauthorized party attempts to unpause -> must fail
    direct_vm.sender = whitehat
    with pytest.raises(Exception):
        contract.resume_protocol(protocol_addr)

    # Authorized protocol lifts halt after fix
    direct_vm.sender = protocol_addr
    contract.resume_protocol(protocol_addr)
    assert contract.is_halted(protocol_addr) is False
