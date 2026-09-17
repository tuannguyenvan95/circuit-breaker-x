# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from dataclasses import dataclass
import json


def _addr_str(addr: Address) -> str:
    """Safely format an Address instance into a lowercase hex string."""
    try:
        return addr.as_hex.lower()
    except Exception:
        return str(addr).lower()


@allow_storage
@dataclass
class ReportRecord:
    """Storage struct representing an emergency exploit report."""
    report_id: str
    target_protocol: Address
    reporter: Address
    evidence_url: str
    staked_amount: bigint
    status: str          # "PENDING", "CONFIRMED", "DISMISSED"
    verdict: str         # "PENDING", "CONFIRMED_EXPLOIT", "FALSE_ALARM"
    reason: str          # Explanation from AI consensus
    created_at: bigint
    resolved_at: bigint


class Contract(gl.Contract):
    """
    CircuitBreakerX: Autonomous On-Chain Exploit Emergency Halt Protocol
    Track: Autonomous Protocols
    Network: GenLayer studionet (Chain ID: 61999)
    """
    owner: Address
    min_stake: bigint
    report_count: bigint
    reports: TreeMap[str, ReportRecord]
    protocol_halted: TreeMap[str, bool]
    registered_protocols: TreeMap[str, bool]
    protocol_bounties: TreeMap[str, bigint]
    treasury_balance: bigint

    def __init__(self):
        # GenVM automatically initializes TreeMap and DynArray storage fields.
        # DO NOT reassign self.reports = TreeMap() in __init__ to prevent AssertionError.
        self.owner = gl.message.sender_address
        self.min_stake = bigint(100)
        self.report_count = bigint(0)
        self.treasury_balance = bigint(0)

    def _parse_llm_json(self, text: str) -> dict:
        """Safely parse LLM responses, stripping any markdown wrappers if present."""
        try:
            cleaned = str(text).strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            return json.loads(cleaned)
        except Exception as e:
            return {
                "verdict": "FALSE_ALARM",
                "confidence": 0,
                "severity": "NONE",
                "reason": f"Failed to parse LLM JSON: {str(e)[:100]}"
            }

    @gl.public.write.payable
    def register_target_protocol(self, protocol: Address) -> None:
        """
        Allow a target protocol or DeFi vault to register for CircuitBreakerX protection.
        Optionally deposit initial bounty funds in GEN to incentivize whitehat reporters.
        """
        prot_str = _addr_str(protocol)
        self.registered_protocols[prot_str] = True

        if prot_str not in self.protocol_halted:
            self.protocol_halted[prot_str] = False

        initial_bounty = bigint(gl.message.value)
        if initial_bounty > bigint(0):
            if prot_str in self.protocol_bounties:
                self.protocol_bounties[prot_str] += initial_bounty
            else:
                self.protocol_bounties[prot_str] = initial_bounty

    @gl.public.write.payable
    def deposit_protocol_bounty(self, protocol: Address) -> None:
        """
        Allow protocol teams, DAOs, or sponsors to fund or top-up the emergency
        whitehat bounty pool for a registered target protocol.
        """
        deposit_amount = bigint(gl.message.value)
        if deposit_amount <= bigint(0):
            raise gl.UserError("Deposit amount must be greater than 0 GEN.")

        prot_str = _addr_str(protocol)
        if prot_str in self.protocol_bounties:
            self.protocol_bounties[prot_str] += deposit_amount
        else:
            self.protocol_bounties[prot_str] = deposit_amount

    @gl.public.write.payable
    def submit_exploit_report(self, target_protocol: Address, evidence_url: str) -> str:
        """
        Security researchers or whitehats submit an exploit report with proof URL.
        Must stake at least min_stake GEN (anti-spam / anti-griefing bond).
        """
        stake = bigint(gl.message.value)
        if stake < self.min_stake:
            raise gl.UserError(f"Insufficient stake. Required minimum is {self.min_stake} GEN.")

        clean_url = evidence_url.strip()
        if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
            raise gl.UserError("evidence_url must begin with http:// or https://")

        prot_str = _addr_str(target_protocol)
        if prot_str in self.protocol_halted and self.protocol_halted[prot_str]:
            raise gl.UserError("Target protocol is already in EMERGENCY HALT state.")

        self.report_count += bigint(1)
        rep_id = str(self.report_count)

        self.reports[rep_id] = ReportRecord(
            report_id=rep_id,
            target_protocol=target_protocol,
            reporter=gl.message.sender_address,
            evidence_url=clean_url,
            staked_amount=stake,
            status="PENDING",
            verdict="PENDING",
            reason="Awaiting decentralized AI Incident Response consensus.",
            created_at=self.report_count,
            resolved_at=bigint(0),
        )

        return rep_id

    @gl.public.write
    def adjudicate_report(self, report_id: str) -> None:
        """
        Triggers decentralized AI Incident Response adjudication across GenLayer validators.
        Validators fetch web evidence via gl.nondet.web.render, perform LLM security assessment,
        and reach consensus on the verdict: CONFIRMED_EXPLOIT vs FALSE_ALARM.
        """
        if report_id not in self.reports:
            raise gl.UserError("Report not found.")

        report = self.reports[report_id]
        if report.status != "PENDING":
            raise gl.UserError("Report has already been adjudicated or is not PENDING.")

        # CRITICAL RULE: Extract all storage values to local variables BEFORE nondet block.
        evidence_url_local = str(report.evidence_url)
        protocol_str_local = _addr_str(report.target_protocol)

        def leader_fn():
            # 1. Fetch web content directly on-chain
            evidence_content = ""
            fetch_error = False
            try:
                res = gl.nondet.web.render(evidence_url_local, mode="text")
                evidence_content = res.content if hasattr(res, "content") else str(res)
            except Exception:
                fetch_error = True

            # 2. Handle inaccessible URLs, blank pages, and 404s
            if fetch_error or not evidence_content or len(evidence_content.strip()) < 20:
                return {
                    "verdict": "FALSE_ALARM",
                    "confidence": 100,
                    "severity": "NONE",
                    "reason": "Evidence URL is inaccessible, empty, or failed to render."
                }

            lower_snippet = evidence_content[:500].lower()
            if any(err in lower_snippet for err in ["404 not found", "error 404", "page not found", "access denied"]):
                return {
                    "verdict": "FALSE_ALARM",
                    "confidence": 100,
                    "severity": "NONE",
                    "reason": "Evidence URL returned 404 Not Found or Access Denied."
                }

            truncated_evidence = evidence_content[:5000]

            # 3. Formulate security incident audit prompt
            prompt = f"""You are an Autonomous AI Incident Response Council on the GenLayer decentralized consensus network.
Your mission is to adjudicate an Emergency Exploit Report filed against smart contract protocol {protocol_str_local}.
Evaluate whether there is verified, credible evidence of an active or recent security exploit, fund drain, reentrancy/flashloan attack, oracle manipulation, or protocol insolvency that justifies an IMMEDIATE EMERGENCY CIRCUIT BREAKER HALT.

TARGET PROTOCOL: {protocol_str_local}

INCIDENT EVIDENCE:
\"\"\"
{truncated_evidence}
\"\"\"

AUDIT CRITERIA:
1. CONFIRMED_EXPLOIT:
   - Definite proof of drained funds, smart contract logic exploitation, reentrancy, bridge drain, flash-loan manipulation, or compromised admin keys.
   - Official security alert from recognized firms (e.g., PeckShield, CertiK, SlowMist, BlockSec, OpenZeppelin) or protocol core developers confirming an ongoing attack.
2. FALSE_ALARM:
   - Normal market volatility, routine liquidations, user mistakes, general educational articles, historical fixed bugs, speculative rumors, or unrelated spam.
   - Any submission lacking concrete evidence of an active or recent critical exploit.

OUTPUT FORMAT:
Respond ONLY with a VALID JSON object (no markdown formatting, no code blocks):
{{
  "verdict": "CONFIRMED_EXPLOIT" or "FALSE_ALARM",
  "confidence": <integer from 0 to 100>,
  "severity": "CRITICAL" or "HIGH" or "NONE",
  "reason": "<concise explanation max 250 characters>"
}}"""

            try:
                raw_res = gl.nondet.exec_prompt(prompt, response_format="json")
                parsed = None
                if isinstance(raw_res, dict):
                    parsed = raw_res
                elif hasattr(raw_res, "content") and isinstance(raw_res.content, dict):
                    parsed = raw_res.content
                else:
                    text = raw_res.content if hasattr(raw_res, "content") else str(raw_res)
                    cleaned = str(text).strip()
                    if cleaned.startswith("```json"):
                        cleaned = cleaned[7:]
                    elif cleaned.startswith("```"):
                        cleaned = cleaned[3:]
                    if cleaned.endswith("```"):
                        cleaned = cleaned[:-3]
                    parsed = json.loads(cleaned.strip())

                verdict = str(parsed.get("verdict", "FALSE_ALARM")).strip().upper()
                if verdict not in ("CONFIRMED_EXPLOIT", "FALSE_ALARM"):
                    verdict = "FALSE_ALARM"

                try:
                    conf = int(parsed.get("confidence", 0))
                    conf = max(0, min(100, conf))
                except Exception:
                    conf = 50

                sev = str(parsed.get("severity", "NONE")).strip().upper()
                if sev not in ("CRITICAL", "HIGH", "NONE"):
                    sev = "NONE"

                reason_str = str(parsed.get("reason", "Incident adjudicated by AI Incident Response Council."))[:250]

                # Low confidence threshold: downgrade to FALSE_ALARM to avoid premature griefing halts
                if verdict == "CONFIRMED_EXPLOIT" and conf < 60:
                    verdict = "FALSE_ALARM"
                    reason_str = f"[Low Confidence: {conf}%] Downgraded: " + reason_str

                return {
                    "verdict": verdict,
                    "confidence": conf,
                    "severity": sev,
                    "reason": reason_str
                }
            except Exception as e:
                return {
                    "verdict": "FALSE_ALARM",
                    "confidence": 0,
                    "severity": "NONE",
                    "reason": f"Execution error in AI evaluation: {str(e)[:100]}"
                }

        def validator_fn(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            leader = leader_res.calldata
            if not isinstance(leader, dict) or "verdict" not in leader:
                return False

            mine = leader_fn()
            # CRITICAL RULE: Compare semantic VERDICT ONLY!
            # Do NOT compare the 'reason' string because LLM text generation differs across validator nodes.
            v_mine = str(mine.get("verdict", "")).strip().upper()
            v_leader = str(leader.get("verdict", "")).strip().upper()
            return v_mine == v_leader

        adjudication_res = gl.vm.run_nondet(leader_fn, validator_fn)
        if isinstance(adjudication_res, dict):
            final_res = adjudication_res
        else:
            final_res = self._parse_llm_json(str(adjudication_res))

        verdict = str(final_res.get("verdict", "FALSE_ALARM")).strip().upper()
        if verdict not in ("CONFIRMED_EXPLOIT", "FALSE_ALARM"):
            verdict = "FALSE_ALARM"

        reason = str(final_res.get("reason", "Consensus concluded."))

        prot_str = _addr_str(report.target_protocol)
        staked_amount = report.staked_amount
        reporter_addr = report.reporter

        report.verdict = verdict
        report.reason = reason
        report.resolved_at = self.report_count

        if verdict == "CONFIRMED_EXPLOIT":
            # 1. Trigger EMERGENCY CIRCUIT BREAKER HALT
            self.protocol_halted[prot_str] = True
            report.status = "CONFIRMED"

            # 2. Whitehat Reward: Full stake refund + protocol bounty pool (if any)
            available_bounty = bigint(0)
            if prot_str in self.protocol_bounties:
                available_bounty = self.protocol_bounties[prot_str]

            bounty_reward = bigint(0)
            if available_bounty > bigint(0):
                bounty_reward = available_bounty
                self.protocol_bounties[prot_str] = bigint(0)

            total_payout = staked_amount + bounty_reward
            self.reports[report_id] = report

            # Send refund and whitehat reward to reporter
            if total_payout > bigint(0):
                gl.get_contract_at(reporter_addr).emit_transfer(value=total_payout)

        else:
            # FALSE_ALARM / SPAM: Slash reporter's staked bond to treasury to prevent griefing
            report.status = "DISMISSED"
            self.treasury_balance += staked_amount
            self.reports[report_id] = report

    @gl.public.write
    def resume_protocol(self, protocol: Address) -> None:
        """
        Lift emergency halt after the protocol vulnerability has been patched.
        Can only be called by the target protocol address or contract owner.
        """
        prot_str = _addr_str(protocol)
        sender_str = _addr_str(gl.message.sender_address)
        owner_str = _addr_str(self.owner)

        if sender_str != owner_str and sender_str != prot_str:
            raise gl.UserError("Unauthorized: Only the protocol itself or contract owner can resume operations.")

        self.protocol_halted[prot_str] = False

    @gl.public.write
    def set_min_stake(self, new_min_stake: int) -> None:
        """Allow contract owner to update minimum required stake."""
        if _addr_str(gl.message.sender_address) != _addr_str(self.owner):
            raise gl.UserError("Only owner can update min stake.")
        if new_min_stake <= 0:
            raise gl.UserError("Min stake must be greater than 0.")
        self.min_stake = bigint(new_min_stake)

    @gl.public.view
    def is_halted(self, protocol: Address) -> bool:
        """Check whether a target protocol is currently under emergency halt."""
        prot_str = _addr_str(protocol)
        if prot_str in self.protocol_halted:
            return self.protocol_halted[prot_str]
        return False

    @gl.public.view
    def get_report(self, report_id: str) -> str:
        """Retrieve full details of an exploit report as a JSON string."""
        if report_id not in self.reports:
            raise gl.UserError("Report not found.")
        r = self.reports[report_id]
        return json.dumps({
            "report_id": r.report_id,
            "target_protocol": _addr_str(r.target_protocol),
            "reporter": _addr_str(r.reporter),
            "evidence_url": r.evidence_url,
            "staked_amount": str(r.staked_amount),
            "status": r.status,
            "verdict": r.verdict,
            "reason": r.reason,
            "created_at": str(r.created_at),
            "resolved_at": str(r.resolved_at),
        })

    @gl.public.view
    def get_protocol_status(self, protocol: Address) -> str:
        """Retrieve protocol protection status, halt state, and active bounty pool."""
        prot_str = _addr_str(protocol)
        is_reg = self.registered_protocols[prot_str] if prot_str in self.registered_protocols else False
        is_halt = self.protocol_halted[prot_str] if prot_str in self.protocol_halted else False
        bounty = str(self.protocol_bounties[prot_str]) if prot_str in self.protocol_bounties else "0"

        return json.dumps({
            "protocol": prot_str,
            "is_registered": is_reg,
            "is_halted": is_halt,
            "bounty_pool": bounty,
        })

    @gl.public.view
    def get_contract_stats(self) -> str:
        """Retrieve high-level contract statistics."""
        return json.dumps({
            "owner": _addr_str(self.owner),
            "min_stake": str(self.min_stake),
            "report_count": str(self.report_count),
            "treasury_balance": str(self.treasury_balance),
        })

    @gl.public.view
    def get_report_count(self) -> int:
        return int(self.report_count)

    @gl.public.view
    def get_min_stake(self) -> int:
        return int(self.min_stake)
