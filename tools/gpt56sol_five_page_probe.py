from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from echo_link_core.polish_quality import CorrectionQualityGate
from echo_link_core.polish import (
    PolishWritingAssistantAdapter,
    create_default_polish_engine,
)
from echo_link_core.polish_types import PolishMode
from echo_link_core.writing_suggestions import WritingSuggestionEngine
from echo_link_app.rewrite_palette import _group_sentence_suggestions
from full_page_tone_quality_probe import TONES, TONE_COMMANDS


@dataclass(frozen=True, slots=True)
class PageFixture:
    page_id: str
    title: str
    source: str
    protected_terms: tuple[str, ...]


MODES = ("Proofread", *TONES)
MODE_COMMANDS = {
    "Proofread": "Proofread",
    **TONE_COMMANDS,
}
NO_EM_DASH_DIRECTION = (
    "Never use an em dash. Use a comma, colon, semicolon, or parentheses instead. "
    "Correct every spelling, grammar, punctuation, vocabulary, and sentence structure error."
)
REPORT_SCHEMA_VERSION = 2
INDEPENDENT_JUDGE_MODEL = "GPT-5.6 Sol"
REQUIRED_JUDGE_SCORE_PERCENT = 100.0


class _ProbeSettingsStore:
    def __init__(self) -> None:
        self._values: dict[str, object] = {}
        self.path = ROOT / "evidence" / "gpt56sol-probe-settings.sqlite"

    def get_meta(self, key: str, default=None):
        return self._values.get(key, default)

    def set_meta(self, key: str, value) -> None:
        self._values[key] = value


def build_manuscript_writer(model: str) -> PolishWritingAssistantAdapter:
    requested = str(model).strip().casefold()
    professional = "12b" in requested
    mode = PolishMode.PROFESSIONAL if professional else PolishMode.BALANCED
    if "e2b" in requested:
        preferred = "gemma4-e2b-qat-v1"
    elif "e4b" in requested:
        preferred = "gemma4-e4b-qat-v1"
    elif "12b" in requested:
        preferred = "gemma4-12b-q4-k-m-v1"
    else:
        preferred = ""
    store = _ProbeSettingsStore()
    engine = create_default_polish_engine(
        store=store,
        preferred_pack_provider=lambda: preferred,
    )
    return PolishWritingAssistantAdapter(
        engine,
        mode_provider=lambda: mode,
        consent_provider=lambda: True,
    )


CORPUS = (
    PageFixture(
        "page-1",
        "Regional clinic power drill",
        """the Redwood Valley Community Clinic response team are preparing a regional power outage drill for thursday, October 8, from 7:15 a.m. to 1:30 p.m. Clinic director Dr. Amina Shah say the exercise will test whether essential services can continue for six hours without utility power, but several department plans was submitted late and they contains conflicting instructions. The drill includes 72 volunteers, 18 medical staff members, and four observers from the County Health Office. Every participants must check in at Room B-214, recieve a yellow badge, and place personal phones inside a sealed locker before orientation begins. Last spring two visitors wandered into a restricted hallway because the temporary signs was vague, nobody reported the problem until the exercise already ended?? Facilities supervisor Owen Price definately wants clearer routes this year he ordered blue arrows for patient areas and orange arrows for supply areas. The backup generator were serviced on September 28 and its fuel tank is 87 percent full, however the maintainance record don't show who completed the final load test. Amina asked Owen to verify the signature by October 6 and keep the original paper record in cabinet RVC-POWER-03; no one may scan it to a personal account or send it through email.

Orientation start at 7:45 a.m. in the Cedar Training Hall, and the first simulated outage is suppose to begin at 8:20 a.m. The response sequence have five stages secure entrances, confirm patient locations, transfer critical equipment, start manual documentation and report capacity. At each stages a team leader records the time, however several stopwatches was missing batteries during yesterdays rehearsal. Nurse coordinator Mei Lin will oversee medication storage while technician Carlos Vega monitors refrigerator FR-22 and freezer FZ-9. Both units must stay inside there approved temperature ranges but MedTrack 4.7 sometimes display a blank reading after users reconnect the sensor cable. Technicians has reproduced the failure on tablet RV-11 and tablet RV-14, they haven't determine whether the cable, tablet port or software cache are responsible. Until the cause is known staff must write readings on form TEMP-108 every fifteen minutes and enter them into MedTrack 4.7 only after utility power returns. Neither erased values or estimated temperatures is allowed, and corrections should include initials the original value and a brief reason. The supply cart currently contains twelve oxygen masks nine flashlights and three radio batteries, although the checklist says it should contains twelve of each item.

the communications plan assigns radio channel 6 to medical teams and channel 9 to facilities, but the printed pocket card reverse those numbers. Owen will destroy the incorrect cards and Priya Desai will print 90 replacements before 5:00 p.m. on October 7. Staff also needs to call 41 patients whose appointments may shift during the drill; the message should explain the possible delay confirm emergency care remains available and provide the clinic's main number. The current script sounds careless it says patients should of planned around the outage even though the drill schedule was not public. At 12:40 p.m. observers will test elevator recall, emergency lighting, oxygen alarms, door locks, the local server and the fire panel connection. A supervisor record each result on checklist RVC-DRILL-1008 and unresolved failures is reported to Amina immediatly. If any alarm fails, if refrigerator FR-22 rises above 46 degrees Fahrenheit, or if generator load drops below 60 percent, normal appointments are paused without exception. After the all clear department leads compares their paper logs with the local server and explains any time gaps duplicate entries or missing signatures. The final report are due October 12; it must preserve patient privacy identify every failed control and state who owns each repair before the clinic schedules another drill.""",
        (
            "Redwood Valley Community Clinic",
            "Dr. Amina Shah",
            "Room B-214",
            "RVC-POWER-03",
            "MedTrack 4.7",
            "TEMP-108",
            "RVC-DRILL-1008",
            "County Health Office",
        ),
    ),
    PageFixture(
        "page-2",
        "Operations software launch",
        """the Northstar Field Services team were suppose to launch DispatchGrid 3.2 on Monday, November 16, but the final review didnt happen like the plan said it would. Project manager Elena Ruiz recieved three seperate checklists and none of them agrees about who owns the last database backup. The installation covers 126 technicians in seven districts and every technician need to sign in with there existing company account. No personal email address can be used, even if a worker says it is more easier. During the pilot, eight tablets loose their saved routes after the battery went below 12 percent, the support team marked the issue as cosmetic when it actually blocked two emergency repairs. Which was a serious mistake. Elena told system administrator Marcus Bell to preserve backup NSFS-DB-441 and verify its SHA-256 value before 6:30 p.m. on November 15. Marcus must not overwrite the earlier backup because finance needs it for the quarterly audit. The team also has to keep customer names, gate codes and service notes out of screenshots that gets attached to public tickets.

Training begins at 8:00 a.m. in Conference Room 5C and remote workers joins through meeting code DG32-LAUNCH. The trainer have forty five minutes to explain route syncing offline notes photo uploads and emergency escalation. Several instruction is wrote with vague vocabulary, for example the guide says users should deal with failed uploads in the normal fashion. That wording has no useful affect on what a technician actually does. The correct process requires workers to keep the original photo, reconnect to the company VPN, press Retry once, and call the service desk if status code DG-409 appears again. They should never delete a photo before the case number is recorded. Last weeks rehearsal was very unique because three trainers talked at once, nobody could understand which screen they was describing and the caption file stopped after nineteen minutes!! The accessibility coordinator Dana Cho requested a corrected caption file by November 18, however the media vendor haven't confirmed if they can meet that date.

after deployment the command center stays open for 36 hours. District leads reports login failures every two hours using form OPS-32, and Marcus compares the totals with server dashboard NS-MON-7. If more then 15 percent of technicians cannot sync routes, Elena pauses the rollout for that district but other districts keep working. The policy is intentionally specific because pausing all seven districts would effect customers who already have working devices. At 10:00 a.m. Tuesday finance will test mileage exports while safety staff checks whether emergency jobs remain first in each queue. The old system cannot be removed until both checks passes and Elena gives written approval. A rushed manager suggested they could remove it irregardless, that arguement was rejected. The final launch note must list every unresolved defect, the person responsible, the next review time and whether customer service was interrupted. It also needs to say that DispatchGrid 3.2 runs locally on managed tablets and does not send private service notes to a personal cloud account. Everyone agree the note should be brief, but leaving out a failure would make it less accurate then the longer version.""",
        (
            "Northstar Field Services",
            "DispatchGrid 3.2",
            "Elena Ruiz",
            "Marcus Bell",
            "NSFS-DB-441",
            "DG32-LAUNCH",
            "DG-409",
            "NS-MON-7",
        ),
    ),
    PageFixture(
        "page-3",
        "Community garden research summary",
        """community gardens is often described like they automatically fix every neighborhood problem, this claim are too simple and it ignores how projects actually operate. The Lakeside University research group studied twelve gardens between March 2024 and February 2025. Lead researcher Professor Nia Okafor say the study did not measure property values and it must not be presented as proof that gardens raise rent. Instead the group counted volunteer hours crop donations canceled workdays and attendance at public meetings. They recorded 4,860 volunteer hours and 7,240 pounds of produce, of which 61 percent went to Community Pantry West. These numbers sounds impressive but quantity by itself don't explain who had access to plots or why some residents stopped participating. Because the survey was only availible in English for the first six weeks. Which caused fewer responses from households that primarily spoke Spanish or Vietnamese. The researchers corrected that problem on April 22 by adding translated forms and hiring Linh Tran and Mateo Silva as paid outreach coordinators.

the report argues that stable leadership was more better predictor of participation then garden size. Gardens with a written schedule and two trained coordinators lost less workdays, while gardens relying on one unpaid organizer had alot more cancellations. This relationship is an association, it is not evidence that leadership alone caused the difference. At Garden L-04 the water line broke for seventeen days and volunteers carried water from a hydrant 300 feet away. At Garden L-09 the city closed the adjacent bus stop, so older participants had fewer practical ways to arrive. Neither event were controlled by garden leaders, and ignoring them would be an incorrect use of the data. The draft discussion section uses the word failure to describe low attendance, but thats poor vocabulary because it sounds like residents was personally responsible for barriers outside their control. A more accurate description would seperate low attendance from program management and explain each documented constraint.

there is also several punctuation and structure problems in the preliminary report. One paragraph lists soil testing, tool storage compost training and language access without commas another paragraph repeats the same conclusion three times in slightly different words. The methods section jumps from recruitment to results and then back to consent procedures, readers gets confused about the actual sequence. Before any interview, participants received form LU-CG-12 and could decline audio recording without loosing access to a garden plot. No recording was shared outside the encrypted project drive. The retention plan requires deletion on August 31, 2026, unless the university ethics board approves a written extension. The conclusion should state that gardens can support food sharing social contact and local learning when organizers have reliable resources. It should not claim gardens eliminate food insecurity, prevent crime or guarantee community unity. Professor Okafor wants the final wording to remain cautious, transparent and useful to city staff. The final report are due May 9, 2025, and the appendix must include the translated survey dates the two transportation disruptions and a table showing all twelve gardens rather than only the six with highest participation.""",
        (
            "Lakeside University",
            "Professor Nia Okafor",
            "Community Pantry West",
            "Linh Tran",
            "Mateo Silva",
            "Garden L-04",
            "Garden L-09",
            "LU-CG-12",
        ),
    ),
    PageFixture(
        "page-4",
        "Technical synchronization incident",
        """on January 14 the HarborPoint Analytics ingestion service started duplicating invoice events after SyncBridge 6.4 was deployed to node HP-EAST-03. The first alert occured at 02:17 UTC, however the overnight operator closed it because CPU use was normal and no server was offline. That conclusion dont follow from the evidence because duplicate events can happen while hardware stays healthy. By 03:05 UTC queue INV-PRIMARY contained 18,442 extra messages and the billing preview showed $286,910.40 more revenue then the source ledger. Incident commander Ravi Morgan declared severity SEV-1 and told the team not to publish invoices, but one scheduled export had already created file INV-2026-0114-A. The file must remain quarantined in vault Q-17, it cannot be emailed, renamed or deleted until Legal approves disposal. Engineer Sofia Chen found that retry worker sb_retry_2 treated HTTP 409 as a new event instead of an existing record. The behavior only appeared when cache key customer_id was blank and feature flag SB_DEDUP_V2 was enabled.

the rollback procedure were harder then expected. Runbook RB-SYNC-64 says disable the feature flag, drain INV-PRIMARY, restart two workers and compare message counts. It doesnt say whether draining should happen before or after producers are paused which is a dangerous ambiguity. Ravi chose to pause producers first at 03:22 UTC so no additional invoice event could enter the queue. Sofia disabled SB_DEDUP_V2 at 03:24 UTC and workers sb_retry_1 and sb_retry_2 restarted at 03:29 UTC. The queue reached zero at 03:41 UTC, but dashboard HP-METRICS-9 still display duplicate totals because its cache refresh every fifteen minutes. While waiting for the refresh. Analyst Jonah Price ran query audit_invoice_delta.sql against a read only replica and confirmed 2,106 customers was affected. He definately did not run the query on production, and the report should not imply that he did. Another engineer wrote that data was corrupt, this vocabulary is inaccurate because source ledger records remained correct while derived queue events were duplicated.

recovery validation used snapshot LEDGER-0315 and checksum 7f21c9aa. Sofia replayed 18,442 valid events from archive ARC-2026-14 after the fixed worker passed test suite SYNC-CI-882. The replay began at 04:10 UTC and finish at 04:46 UTC with zero duplicate identifiers. Finance compared 250 sampled invoices and found no amount differences, customer names or tax codes. At 05:00 UTC Ravi allowed invoice generation to resume but he kept external delivery disabled until finance director Mara Singh signed approval at 06:12 UTC. Several sentences in the draft postmortem is fragments, other ones combine six actions with commas and no clear order, and the word utilize is used when use would be clearer. The root cause statement must say the deployment exposed an idempotency defect in retry handling. It must not blame the overnight operator as the technical cause, although the alert closure delayed detection by 48 minutes. Follow up actions include adding a 409 regression test, defining the producer pause order in RB-SYNC-64, and changing alert HP-DUP-4 so duplicate growth triggers even when CPU is normal. Owners and deadlines remains required in the final table, and no customer data may appear in the public version.""",
        (
            "HarborPoint Analytics",
            "SyncBridge 6.4",
            "HP-EAST-03",
            "INV-PRIMARY",
            "INV-2026-0114-A",
            "SB_DEDUP_V2",
            "RB-SYNC-64",
            "audit_invoice_delta.sql",
        ),
    ),
    PageFixture(
        "page-5",
        "Library evening program proposal",
        """the Eastgate Public Library should extend its weekday study program until 9:30 p.m. for a twelve week trial, because students and shift workers currently has no quiet public space after 7:00 p.m. The proposal is not asking for permanent hours and it do not promise that every visitor will use the service. Branch manager Keisha Ward recieved 684 survey responses between January 5 and February 2, 438 respondents said they would use later hours at least twice a month. The strongest interest came from community college students hospital employees and parents attending evening certification classes. This data is useful but it should of been explained with more caution because a survey shows interest, not guaranteed attendance. The proposal also says the program will be completely free, which is incorrect vocabulary since the library expects operating costs of $18,600. Visitors will not pay a fee, but the city still pays staff security utilities and transportation support.

the trial would run from April 6 through June 26 at the Eastgate Central branch only. It requires two library assistants one security officer and one on call supervisor each night. No employee may work beyond there contract limits, and participation in extra shifts must be voluntary. Keisha has a tentative agreement with Route 14 Transit to hold the final bus until 9:45 p.m. Monday through Thursday, however the transit board must approve schedule RT14-EG-2026 on March 18. If the board rejects it the library cannot advertise late transportation as confirmed. The safety plan adds a staffed entrance, hourly floor checks and a text line monitored by City Safety Desk. One draft sentence claims these measures eliminate all risk, that is a ridiculous arguement because no plan can guarantee zero incidents. A more accurate sentence would say the measures reduce known access and supervision risks. While making clear that emergency procedures remain unchanged.

some council members says the program is too expensive, but the current draft responds in a hostile way and calls there concern shortsighted. That tone weakens the proposal and it dont answer the budget question. The $18,600 estimate includes $11,200 for staffing, $3,400 for security, $2,000 for utilities and $2,000 for transit coordination. Funding would come from grant EG-LRN-55, not the general book budget. If costs exceed the grant the program must stop unless council approves another source in public session. Success measures include nightly attendance, repeat visits, reported safety issues, staff overtime and the percentage of visitors who arrive or leave by bus. The library will publish aggregate totals but must not release names, library card numbers or individual travel patterns. Keisha will present a midpoint review on May 18 and a final report on July 10. The final recommendation should be persuasive but honest, it should explain why the limited trial is worth testing without pretending the evidence is more stronger then it is. Council retains the final decision, and approval of the trial does not authorize permanent hours after June 26.""",
        (
            "Eastgate Public Library",
            "Keisha Ward",
            "Eastgate Central",
            "Route 14 Transit",
            "RT14-EG-2026",
            "City Safety Desk",
            "EG-LRN-55",
            "June 26",
        ),
    ),
)


_SOURCE_DEFECTS = {
    "spelling": re.compile(
        r"\b(?:recieve|recieved|definately|maintainance|didnt|doesnt|availible|"
        r"seperate|loosing|occured|immediatly|yesterdays|thats)\b",
        re.IGNORECASE,
    ),
    "grammar": re.compile(
        r"\b(?:team are|team were suppose|plans was|they contains|every participants|"
        r"orientation start|sequence have|each stages|technicians has|staff also needs|"
        r"supervisor record|failures is|leads compares|report are|none of them agrees|"
        r"technician need|workers joins|trainer have|instruction is wrote|district leads reports|"
        r"checks passes|everyone agree|gardens is|claim are|researcher say|numbers sounds|"
        r"there is also several|readers gets|conclusion dont|procedure were|"
        r"dashboard [^.!?]{0,80} display|customers was|"
        r"replay began[^.!?]{0,80}\bfinish\b|"
        r"several sentences in the draft postmortem is|owners and deadlines remains|"
        r"workers currently has|it do not|members says|it dont)\b",
        re.IGNORECASE,
    ),
    "punctuation": re.compile(
        r"\s+[,.!?;:]|[!?]{2,}|(?<!\b(?-i:[A-Z]))\.(?=(?-i:[A-Z]))|"
        r"(?<!however),\s*(?:this claim|438 respondents|nobody|the support team|it cannot|"
        r"that tone|that is a|another paragraph|readers)\b",
        re.IGNORECASE,
    ),
    "vocabulary": re.compile(
        r"\b(?:should of|more easier|very unique|irregardless|more better|"
        r"more stronger|normal fashion|ridiculous arguement|completely free)\b|"
        r"\bdata was corrupt,\s*this vocabulary\b",
        re.IGNORECASE,
    ),
    "sentence_structure": re.compile(
        r"(?:\A|(?<=[.!?])\s+)(?:Which was\b|"
        r"Because the survey[^.!?]{0,100}[.!?]|"
        r"While waiting[^,.!?]{0,70}[.!?]|"
        r"While making clear[^,.!?]{0,100}[.!?])|"
        r",\s*(?:nobody|the support team|readers|that arguement|this vocabulary)\b",
        re.IGNORECASE,
    ),
}


def scan_source_defects(text: str) -> dict[str, int]:
    return {
        category: len(tuple(pattern.finditer(text)))
        for category, pattern in _SOURCE_DEFECTS.items()
    }


def static_output_gate(
    text: str,
    *,
    protected_terms: tuple[str, ...],
) -> dict[str, object]:
    defects = scan_source_defects(text)
    missing_terms = tuple(value for value in protected_terms if value not in text)
    em_dash_count = text.count("\u2014")
    quality = CorrectionQualityGate().evaluate(text, command="Proofread")
    failures = {
        "known_defects": defects,
        "missing_protected_terms": missing_terms,
        "quality_residual_errors": quality.residual_errors,
        "em_dash_count": em_dash_count,
    }
    passed = bool(
        not any(defects.values())
        and not missing_terms
        and not quality.residual_errors
        and em_dash_count == 0
    )
    return {
        "check_type": "static_heuristic",
        "passed": passed,
        "static_check_score_percent": 100.0 if passed else 0.0,
        "independent_model_judgment": False,
        **failures,
    }


def _judgment_error(value: object) -> str:
    """Explain why a value is not a completed independent GPT judgment."""
    if not isinstance(value, dict):
        return "missing"
    if value.get("status") != "completed":
        return "status is not completed"
    if value.get("judge_model") != INDEPENDENT_JUDGE_MODEL:
        return "judge model is not GPT-5.6 Sol"
    if value.get("independent") is not True:
        return "judgment is not marked independent"
    if not str(value.get("judge_run_id", "")).strip():
        return "judge run id is missing"
    if not str(value.get("judged_at", "")).strip():
        return "judged timestamp is missing"
    score = value.get("score_percent")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return "judge score is missing"
    if not 0.0 <= float(score) <= 100.0:
        return "judge score is outside 0 to 100"
    if not isinstance(value.get("passed"), bool):
        return "judge pass result is missing"
    if not isinstance(value.get("findings"), list):
        return "judge findings are missing"
    return ""


def _case_judgment(case: dict) -> object:
    return case.get(
        "independent_gpt56sol_judgment",
        case.get("gpt56sol_judgment"),
    )


def _upgrade_report_schema(report: dict) -> dict:
    """Migrate old reports without treating their placeholders as judgments."""
    report["schema_version"] = REPORT_SCHEMA_VERSION
    report.pop("judge_model", None)
    report.pop("threshold_percent", None)
    report.pop("runtime_passed", None)
    report.pop("runtime_passed_count", None)
    report["independent_judge"] = {
        "requested_model": INDEPENDENT_JUDGE_MODEL,
        "required": True,
        "required_score_percent": REQUIRED_JUDGE_SCORE_PERCENT,
        "required_case_fields": [
            "status",
            "judge_model",
            "independent",
            "judge_run_id",
            "judged_at",
            "score_percent",
            "passed",
            "findings",
        ],
    }
    for case in report.get("cases", ()):
        if "runtime_static_passed" not in case and "runtime_passed" in case:
            case["runtime_static_passed"] = bool(case["runtime_passed"])
        case.pop("runtime_passed", None)
        if "independent_gpt56sol_judgment" not in case:
            case["independent_gpt56sol_judgment"] = case.get(
                "gpt56sol_judgment"
            )
        case.pop("gpt56sol_judgment", None)
    return report


def _update_report_summary(report: dict, *, expected_case_count: int) -> None:
    """Set honest runtime, independent-judge, and final gate summaries."""
    _upgrade_report_schema(report)
    cases = list(report.get("cases", ()))
    case_count = len(cases)
    runtime_static_passed_count = sum(
        bool(case.get("runtime_static_passed")) for case in cases
    )
    report["case_count"] = case_count
    report["expected_case_count"] = expected_case_count
    report["runtime_static_passed_count"] = runtime_static_passed_count
    report["runtime_static_passed"] = bool(
        case_count == expected_case_count
        and runtime_static_passed_count == case_count
    )

    judgment_errors: list[dict[str, str]] = []
    completed_judgments: list[dict] = []
    for case in cases:
        judgment = _case_judgment(case)
        error = _judgment_error(judgment)
        if error:
            judgment_errors.append(
                {
                    "mode": str(case.get("mode", "")),
                    "page_id": str(case.get("page_id", "")),
                    "error": error,
                }
            )
        else:
            completed_judgments.append(judgment)
    judged_count = len(completed_judgments)
    judgment_complete = bool(
        case_count == expected_case_count and judged_count == case_count
    )
    judgment_passed_count = sum(
        bool(value["passed"])
        and float(value["score_percent"]) >= REQUIRED_JUDGE_SCORE_PERCENT
        for value in completed_judgments
    )
    judgment_passed = bool(
        judgment_complete and judgment_passed_count == case_count
    )
    report["independent_judged_count"] = judged_count
    report["independent_judgment_complete"] = judgment_complete
    report["independent_judgment_passed_count"] = judgment_passed_count
    report["independent_judgment_passed"] = judgment_passed
    report["independent_judgment_errors"] = judgment_errors
    report["independent_judge_score_percent"] = (
        min(float(value["score_percent"]) for value in completed_judgments)
        if judgment_complete
        else None
    )
    final_passed = bool(report["runtime_static_passed"] and judgment_passed)
    report["final_passed"] = final_passed
    report["final_score_percent"] = (
        100.0 if final_passed else None
    )
    if case_count < expected_case_count:
        status = "runtime_incomplete"
    elif not report["runtime_static_passed"]:
        status = "runtime_or_static_checks_failed"
    elif not judgment_complete:
        status = "awaiting_independent_gpt56sol_judgment"
    elif not judgment_passed:
        status = "independent_gpt56sol_judgment_failed"
    else:
        status = "passed"
    report["completion_status"] = status


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _save_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_page_through_hud(
    writer,
    *,
    source: str,
    command: str,
    model: str,
    protected_terms: tuple[str, ...],
    timeout: float,
) -> tuple[str, list[dict[str, object]]]:
    """Run the ordered one-to-three-sentence path used by the Word HUD."""
    atomic = WritingSuggestionEngine().analyze(source)
    review_sections = _group_sentence_suggestions(source, atomic)
    if not review_sections:
        raise RuntimeError("The HUD did not create any review sections.")
    output: list[str] = []
    evidence: list[dict[str, object]] = []
    cursor = 0
    total = len(review_sections)
    for index, section in enumerate(review_sections, start=1):
        start = int(section.start)
        end = int(section.end)
        original = str(section.original)
        prepared = str(section.replacement)
        if source[start:end] != original:
            raise RuntimeError("The HUD review section no longer matches its source.")
        section_terms = tuple(
            term for term in protected_terms if term in original
        )
        result = writer.run(
            prepared,
            command=command,
            model=model,
            instruction=NO_EM_DASH_DIRECTION,
            profile="General",
            protected_terms=section_terms,
            timeout=timeout,
            keep_alive=300,
            context_window=4096,
            max_tokens=768,
            require_gpu=True,
        )
        polish_result = getattr(writer, "last_correction_result", None)
        if polish_result is not None and not bool(
            getattr(polish_result, "quality_complete", False)
        ):
            message = (
                str(getattr(polish_result, "message", "")).strip()
                or "Full AI correction did not pass final quality checks."
            )
            residual = ", ".join(
                str(value)
                for value in getattr(polish_result, "residual_errors", ())
            )
            validation = getattr(polish_result, "validation", None)
            failures = ", ".join(
                str(value)
                for value in getattr(validation, "failures", ())
            )
            detail = " ".join(
                value
                for value in (
                    f"Residual: {residual}." if residual else "",
                    f"Validation: {failures}." if failures else "",
                )
                if value
            )
            raise RuntimeError(
                f"HUD section {index} of {total}: {message}"
                + (f" {detail}" if detail else "")
            )
        corrected = result.text
        output.append(source[cursor:start])
        output.append(corrected)
        cursor = end
        evidence.append(
            {
                "index": index,
                "total": total,
                "start": start,
                "end": end,
                "sentence_count": int(section.sentence_count),
                "protected_terms": section_terms,
                "original": original,
                "prepared": prepared,
                "output": corrected,
                "quality_complete": (
                    bool(getattr(polish_result, "quality_complete", False))
                    if polish_result is not None
                    else None
                ),
                "quality_score": (
                    float(getattr(polish_result, "quality_score", 0.0))
                    if polish_result is not None
                    else None
                ),
                "residual_errors": (
                    tuple(getattr(polish_result, "residual_errors", ()))
                    if polish_result is not None
                    else ()
                ),
                "actual_model": str(getattr(result, "model", "")),
            }
        )
    output.append(source[cursor:])
    return "".join(output), evidence


def run_probe(
    *,
    model: str,
    modes: tuple[str, ...],
    pages: tuple[PageFixture, ...],
    output: Path,
    timeout: float,
    resume: bool,
) -> dict:
    if resume and output.exists():
        report = json.loads(output.read_text(encoding="utf-8"))
    else:
        report = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "probe": "gpt56sol_five_page_manuscript_quality",
            "author_model": "GPT-5.6 Sol",
            "writer_model": model,
            "em_dash_allowed": False,
            "started_at": _utc_now(),
            "source_pages": [
                {
                    "page_id": page.page_id,
                    "title": page.title,
                    "source": page.source,
                    "protected_terms": page.protected_terms,
                    "source_defects": scan_source_defects(page.source),
                }
                for page in pages
            ],
            "cases": [],
        }
    _upgrade_report_schema(report)
    expected_case_count = len(modes) * len(pages)
    completed = {
        (str(case.get("mode")), str(case.get("page_id")))
        for case in report.get("cases", ())
        if case.get("writer_model") == model
    }
    writer = build_manuscript_writer(model)
    for mode in modes:
        command = MODE_COMMANDS[mode]
        for page in pages:
            key = (mode, page.page_id)
            if key in completed:
                continue
            case = {
                "mode": mode,
                "command": command,
                "page_id": page.page_id,
                "writer_model": model,
                "started_at": _utc_now(),
            }
            try:
                corrected, sections = run_page_through_hud(
                    writer,
                    source=page.source,
                    command=command,
                    model=model,
                    protected_terms=page.protected_terms,
                    timeout=timeout,
                )
                gate = static_output_gate(
                    corrected,
                    protected_terms=page.protected_terms,
                )
                case.update(
                    {
                        "output": corrected,
                        "hud_sections": sections,
                        "static_gate": gate,
                        "runtime_static_passed": bool(gate["passed"]),
                        "independent_gpt56sol_judgment": None,
                    }
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                case.update(
                    {
                        "output": "",
                        "runtime_static_passed": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "independent_gpt56sol_judgment": None,
                    }
                )
            case["finished_at"] = _utc_now()
            report["cases"].append(case)
            _update_report_summary(
                report,
                expected_case_count=expected_case_count,
            )
            _save_report(output, report)
            state = "PASS" if case.get("runtime_static_passed") else "FAIL"
            print(
                f"{mode} {page.page_id}: runtime/static {state}",
                flush=True,
            )
    report["finished_at"] = _utc_now()
    _update_report_summary(
        report,
        expected_case_count=expected_case_count,
    )
    _save_report(output, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run five GPT-5.6 Sol error pages through every Manuscript style. "
            "The report preserves full AI output for GPT-5.6 Sol judging."
        )
    )
    parser.add_argument("--model", default="auto")
    parser.add_argument(
        "--modes",
        default=",".join(MODES),
        help="Comma-separated subset of Proofread and visible styles",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--pages",
        default=",".join(page.page_id for page in CORPUS),
        help="Comma-separated page ids",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evidence" / "gpt56sol-five-page-quality.json",
    )
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    modes = tuple(value.strip() for value in args.modes.split(",") if value.strip())
    if not modes or any(value not in MODES for value in modes):
        parser.error("--modes contains an unknown mode")
    page_ids = tuple(
        value.strip() for value in args.pages.split(",") if value.strip()
    )
    known_pages = {page.page_id: page for page in CORPUS}
    if not page_ids or any(value not in known_pages for value in page_ids):
        parser.error("--pages contains an unknown page id")
    report = run_probe(
        model=args.model,
        modes=modes,
        pages=tuple(known_pages[value] for value in page_ids),
        output=args.output,
        timeout=max(5.0, min(180.0, args.timeout)),
        resume=args.resume,
    )
    print(
        "Five-page Manuscript runtime/static gate: "
        f"{report['runtime_static_passed_count']}/{report['case_count']}",
        flush=True,
    )
    print(
        "Independent GPT-5.6 Sol judgments: "
        f"{report['independent_judged_count']}/{report['case_count']}",
        flush=True,
    )
    print(
        f"Final gate: {report['completion_status']}",
        flush=True,
    )
    print(args.output, flush=True)
    return 0 if report["final_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
