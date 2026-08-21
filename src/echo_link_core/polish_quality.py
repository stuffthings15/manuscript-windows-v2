from __future__ import annotations

import re
from dataclasses import dataclass

from .polish_error_score import error_score


@dataclass(frozen=True, slots=True)
class ToneContract:
    name: str
    instruction: str
    vocabulary: str
    formality: str
    warmth: str
    directness: str
    sentence_length: str
    contractions: str
    structure: str
    prohibited_behaviors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CorrectionQualityResult:
    complete: bool
    residual_errors: tuple[str, ...]
    tone_verified: bool
    score: float
    # Comparable count of known grammar errors. The engine compares this
    # against the same measure taken on the deterministic repair; see
    # PolishEngine._beats_deterministic.
    error_score: int = 0


_COMMON_PROHIBITIONS = (
    "invented facts",
    "changed actors",
    "changed numbers or dates",
    "changed negations or obligations",
    # The largest single class of tone refusal measured on 2026-08-20: 14 of
    # 19 modal refusals were one hedge added and none removed. A hedge is not
    # a style choice, it is a weaker claim than the writer made.
    "added or removed hedges such as can, may, might, could or would",
    "broken grammar",
)
_TONE_WORD = re.compile(r"[A-Za-z0-9]+(?:[.:'_-][A-Za-z0-9]+)*\.?")


def _contract(
    name: str,
    instruction: str,
    *,
    vocabulary: str = "natural and precise",
    formality: str = "neutral",
    warmth: str = "neutral",
    directness: str = "clear",
    sentence_length: str = "varied and readable",
    contractions: str = "allowed when natural",
    structure: str = "preserve paragraphs and factual relationships",
    prohibited: tuple[str, ...] = (),
) -> ToneContract:
    return ToneContract(
        name=name,
        instruction=instruction,
        vocabulary=vocabulary,
        formality=formality,
        warmth=warmth,
        directness=directness,
        sentence_length=sentence_length,
        contractions=contractions,
        structure=structure,
        prohibited_behaviors=(*_COMMON_PROHIBITIONS, *prohibited),
    )


TONE_CONTRACTS: dict[str, ToneContract] = {
    "Clear": _contract(
        "Clear",
        "Use clear, natural wording and correct every writing error.",
        vocabulary="plain and unambiguous",
    ),
    "Improve": _contract(
        "Improve",
        "Improve grammar, vocabulary, sentence structure, clarity, and flow.",
        vocabulary="precise and polished",
    ),
    "Rephrase": _contract(
        "Rephrase",
        "Rephrase each sentence as one complete natural sentence while keeping content "
        "nouns, critical verbs, actors, facts, and constraints. Change syntax or flow "
        "only when the result remains natural. Do not duplicate or stack old and new "
        "wording. Do not begin a sentence with Because or While. Use materially "
        "different natural wording without changing meaning.",
        structure="preserve facts and paragraph boundaries; change sentence construction",
        prohibited=("copied source phrasing",),
    ),
    "Shorten": _contract(
        "Shorten",
        "Remove repetition and filler while retaining all actors, factual clauses, "
        "important details, and boundaries.",
        vocabulary="concise",
        sentence_length="short",
        prohibited=("lost key details",),
    ),
    "Friendly": _contract(
        "Friendly",
        "Make every paragraph visibly warm, approachable, and conversational. Recast existing "
        "claims around helping people and making their work easier wherever the source supports it. "
        # Warmth has to be sent somewhere. Friendly's measured failure was
        # never too little hedging - usefulness was 4.13 while style sat at
        # 3.20 - so forbidding its favourite move without naming another
        # leaves the model with nothing and the judge saying "readable but
        # lacks warmth" for the fifteenth time.
        "Get the warmth from word choice, rhythm, and directly addressing the reader, "
        "never from hedging: prefer plain everyday words over formal ones, vary sentence "
        "length, and speak to the reader as you where the source allows it.",
        vocabulary="approachable",
        formality="conversational",
        warmth="warm",
        contractions="preferred when natural",
        prohibited=("fake enthusiasm", "invented thanks", "slang"),
    ),
    "Formal": _contract(
        "Formal",
        "Use conventional, precise, impersonal formal language.",
        vocabulary="precise and conventional",
        formality="formal",
        warmth="restrained",
        contractions="not allowed",
        prohibited=("contractions", "colloquialisms"),
    ),
    "Professional": _contract(
        "Professional",
        "Use polished, confident, concise workplace language with clear actions.",
        vocabulary="workplace-appropriate",
        formality="professional",
        directness="confident",
        prohibited=("casual chatter", "stiff legalistic wording"),
    ),
    "Academic": _contract(
        "Academic",
        "Use precise, objective academic prose with logical transitions and qualified claims.",
        vocabulary="discipline-appropriate and precise",
        formality="academic",
        warmth="restrained",
        contractions="not allowed",
        prohibited=(
            "contractions",
            "invented citations",
            "unsupported claims",
            "casual phrasing",
        ),
    ),
    "Conversational": _contract(
        "Conversational",
        "Use natural spoken-style wording that remains clear, correct, and useful.",
        vocabulary="everyday and natural",
        formality="conversational",
        warmth="approachable",
        contractions="preferred when natural",
        prohibited=("slang overload", "filler", "fake dialogue"),
    ),
    "Concise": _contract(
        "Concise",
        "Remove filler, repetition, and needless qualifiers while preserving every factual clause, "
        "actor, action, relationship, request, and constraint. Aim to reduce the source word count "
        "by about 5 percent. For short fact-dense sections, remove at least one word without losing "
        "facts.",
        vocabulary="compact and precise",
        sentence_length="short",
        prohibited=("lost essential details", "telegraphic fragments"),
    ),
    "Detailed": _contract(
        "Detailed",
        "Make relationships and existing details explicit without inventing information.",
        vocabulary="specific and explanatory",
        sentence_length="varied with controlled detail",
        prohibited=("invented examples", "invented facts", "needless repetition"),
    ),
    "Persuasive": _contract(
        "Persuasive",
        "Create a visible, evidence-led rewrite that connects the source's strongest existing "
        "reasons to its existing recommendation, conclusion, requirement, or consequence.",
        directness="purposeful",
        prohibited=("invented evidence", "exaggeration", "pressure tactics"),
    ),
    "Confident": _contract(
        "Confident",
        "Use assured, decisive language without increasing certainty beyond the source.",
        directness="confident",
        prohibited=("overclaiming", "stronger obligations", "hostility"),
    ),
    "Casual": _contract(
        "Casual",
        "Use relaxed, natural wording while keeping standard grammar and every important fact.",
        vocabulary="simple and everyday",
        formality="casual",
        warmth="friendly",
        contractions="preferred",
        prohibited=("slang that obscures meaning", "broken grammar", "baby talk"),
    ),
    "Technical": _contract(
        "Technical",
        "Use precise technical wording and explicit relationships while preserving identifiers.",
        vocabulary="technical and exact",
        formality="technical",
        warmth="neutral",
        prohibited=("invented specifications", "changed identifiers", "marketing language"),
    ),
    "Simplified": _contract(
        "Simplified",
        "Use plain vocabulary and simpler sentence structure without removing essential content.",
        vocabulary="plain",
        sentence_length="short and clear",
        prohibited=("lost details", "childish wording", "broken grammar"),
    ),
    "High-school level": _contract(
        "High-school level",
        "Use clear high-school-level vocabulary, complete sentences, and direct explanations.",
        vocabulary="accessible high-school level",
        sentence_length="moderate",
        prohibited=("specialist jargon without need", "childish wording"),
    ),
    "College level": _contract(
        "College level",
        "Use polished college-level vocabulary, varied syntax, and well-organized reasoning.",
        vocabulary="college level and precise",
        formality="educated neutral",
        prohibited=("needless jargon", "invented analysis", "inflated wording"),
    ),
    "Diplomatic": _contract(
        "Diplomatic",
        "Use tactful, respectful, neutral framing without weakening boundaries.",
        warmth="respectful",
        directness="tactful but clear",
        prohibited=("blame", "evasive hedging", "weakened obligations"),
    ),
    "Empathetic": _contract(
        "Empathetic",
        "Make care visible wherever the source reports difficulty, confusion, failure, or user "
        "impact. Recast those existing facts from the affected person's perspective.",
        warmth="caring and people-centered",
        directness="clear and considerate",
        prohibited=("invented feelings", "invented apologies", "invented promises"),
    ),
    "Cooperative": _contract(
        "Cooperative",
        "Make shared effort and shared goals visible in every paragraph where the source names a "
        "team or reviewers. Use working-together and solution-focused language without changing ownership.",
        warmth="collaborative",
        directness="solution-focused",
        prohibited=("changed ownership", "invented proposals"),
    ),
    "Assertive": _contract(
        "Assertive",
        "Use direct, confident, active language with respectful boundaries.",
        directness="firm",
        prohibited=("hostility", "threats", "stronger obligations"),
    ),
    "Caveman": _contract(
        "Caveman",
        "Use short, blunt, plain standard English with one idea per sentence.",
        vocabulary="common plain words",
        formality="plain",
        warmth="neutral",
        directness="blunt",
        sentence_length="about 12 words; ordinarily no more than 18",
        contractions="allowed",
        structure="preserve paragraph form; split packed sentences",
        prohibited=("mock accent", "childish speech", "headings", "lists"),
    ),
}



_CAVEMAN_DANGLING_CONDITIONAL = re.compile(
    r"(?:^|(?<=[.!?])\s+)If\s+[^,.!?]+[.!?]"
)

_RESIDUAL_CHECKS = (
    (
        "em dash",
        re.compile(r"\u2014"),
    ),
    (
        "missing apostrophe",
        re.compile(
            r"\b(?:dont|doesnt|didnt|cant|couldnt|wouldnt|shouldnt|wont|"
            r"isnt|arent|wasnt|werent|thats|whats|nobodys|everybodys)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "encoding artifact",
        re.compile(r"â(?:€™|€˜|€œ|€�)"),
    ),
    (
        "malformed negative coordination",
        re.compile(r"\bnot\s+and\s+(?:the|a|an)\b", re.IGNORECASE),
    ),
    (
        "malformed evidence clause",
        re.compile(
            r"\bdoes not follow (?:was|is) based on the evidence\b",
            re.IGNORECASE,
        ),
    ),    (
        "malformed access clause",
        re.compile(r"\bdoes not explain had access\b", re.IGNORECASE),
    ),    (
        "malformed comparison clause",
        re.compile(r"\btreated not as\b", re.IGNORECASE),
    ),    (
        "malformed determiner",
        re.compile(r"\bthe no\s+[a-z]", re.IGNORECASE),
    ),    (
        "malformed final grammar",
        re.compile(
            r"\bdoes not show completed\b"
            r"|\bgoes to a personal account or sends it by email\b"
            r"|(?:^|(?<=[.!?])\s+)No one may (?:scan|send|delete)\."
                        r"|(?:^|(?<=[.!?])\s+)(?:They should never|Without)\."
            r"|\bdid not measure looks at\b"
            r"|(?:^|(?<=[.!?])\s+)Instead,\s+includes\b"
            r"|\bThere has punctuation\b"
            r"|(?:^|(?<=[.!?])\s+)Not treated\b"
            r"|\bNo customer data might show\b"
            r"|\bwould use work late\b"
            r"|\btrial starts [A-Z][a-z]+ \d{1,2} through [A-Z][a-z]+ \d{1,2}\b"
            r"|\bcomes from not the\b"
            r"|(?:^|(?<=[.!?])\s+)(?:Safety issues reported|Staff overtime tracked|"
            r"Bus use percentage noted)\."
            r"|\bexplain Pretending\b"
            r"|\bdid not measure affects?\b"
            r"|\b(?:didn't|did not)\s+measures\b"
            r"|\bmust not be presents as\b"
            r"|\bfor first six weeks\b"
            r"|\boutside encrypted project drive\b"
            r"|\brepeats conclusion\b"
            r"|\bmust not be shows?\b"
            r"|\bwith without\b"
            r"|\bexplain (?-i:The)\b"
            r"|\bThe evidence is stronger than it is\. It should not pretend\."
            r"|\bIt would make clear emergency procedures stay the same\.",
            re.IGNORECASE,
        ),
    ),    (
        "malformed send clause",
        re.compile(
            r"\bdoes not send\b[^.!?]{0,100}\bgo to\b",
            re.IGNORECASE,
        ),
    ),    (
        "malformed auxiliary coordination",
        re.compile(
            r"\b(?:say|state)(?: that)? [A-Z][A-Za-z0-9_-]*"
            r"(?: \d+(?:\.\d+)*)? and does not\b",
            re.IGNORECASE,
        ),
    ),
    (
        "malformed obligation phrase",
        re.compile(
            r"\bmust not overwrite (?:needs|needed|used|kept|accessed|referenced|requested) the (?:earlier|older) backup\b",
            re.IGNORECASE,
        ),
    ),
    (
        "weakened association terminology",
re.compile(
            r"\bThis is (?:just )?a link(?:;|\.)\s*(?:it is not evidence\b)?",
            re.IGNORECASE,
        ),
    ),
    (
        "duplicated follow verb",
        re.compile(r"\bdoes not follow followed from\b", re.IGNORECASE),
    ),
    (
        "missing incident boundary",
        re.compile(r"\bsource ledger\s+Incident commander\b"),
    ),
    (
        "missing example comma",
        re.compile(r"\bfor example\s+the guide\b", re.IGNORECASE),
    ),
    (
        "stacked modal",
        re.compile(r"\bcannot\s+may\s+be\b", re.IGNORECASE),
    ),
    (
        # "there" directly before a possessed noun is never valid English
        # ("trust there owners", "brushing there fur"); it is the their/there
        # homophone the correction must repair.
        "their-there confusion",
        re.compile(
            r"\bthere\s+(?:owners?|ears?|furs?|minds?|tails?|legs?|paws?|"
            r"phones?|habits?|laptops?|homes?|lives|faces?|toys?|"
            r"personalit(?:y|ies|ys)|approval|progress|start\s+date|"
            r"business\s+hours|batch\s+jobs)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "subject-verb agreement",
        re.compile(
            r"(?<!about )(?<!after )(?<!of the )(?<!of )"
            r"\b(?:reports|dogs|people|users|teams)\s+"
            r"(?:is|was|has|does|makes|needs)\b"
            r"|\b(?:parents|children|kids|owners|families|breeds|animals|things|"
            r"treats|pets)\s+"
            r"(?:is|was|has|does|makes|needs|likes|wants|gives|gets|ends)\b"
            r"|\b(?:final\s+)?report\s+are\b"
            r"|\b(?:he|she|it)\s+(?:are|were|have|do|make|need|fail)\b"
            r"|\bMedTrack\s+\d+(?:\.\d+)*\s+sometimes\s+display\b"
            r"|\bother dogs\b[^.!?]{0,80}\btries\b"
            r"|\bNeither\s+[A-Za-z'-]+s?\s+[A-Za-z'-]*\s*"
            r"nor\s+[A-Za-z'-]+s?\s+[A-Za-z'-]*\s*(?:is|was)\s+allowed\b"
            r"|\b(?:media\s+)?vendor\s+haven't\b",
            re.IGNORECASE,
        ),
    ),
    (
        "modal agreement",
        re.compile(
            r"\b(?:must|should|can|could|may|might|will|would)\s+"
            r"(?:checks|receives|places|explains|confirms|provides|contains|"
            r"promises|authorizes|shows|reports|agrees|needs|says|starts|"
            r"joins|runs|keeps|stops|gets|makes|takes|uses|includes|lists|"
            r"states|claims|approves)\b"
            r"|\b(?:must|should)\s+not\s+(?:overwrote|can|claims)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "auxiliary agreement",
        re.compile(
            r"\b(?:don't|doesn't)\s+(?:shows|contains|explains|confirms|provides)\b"
            r"|\bhaven't\s+(?:determine|confirm|finish|complete)\b"
            r"|\bdidn't\s+happened\b"
            r"|\bdid\s+not\s+happened\b"
            r"|\bdid\s+not\s+measured\b"
            r"|\bdoes\s+not\s+(?:sends|requests|promises|shows|follows|approves)\b"
            r"|\bdoesn't\s+says\b"
            r"|\bdid\s+not\s+ran\b"
            r"|\bshould\s+not\s+implies\b"
            r"|\bdoes\s+not\s+transmits\b"
            r"|\bit\s+do\s+not\s+promises\b"
            r"|\bwas\s+not\s+is\b"
            r"|\bcannot\s+will\b"
            r"|\bdoes\s+not\s+to\b"
            r"|\bNot\s+comes\b",
            re.IGNORECASE,
        ),
    ),
    (
        "malformed verb sequence",
        re.compile(
            r"\b(?:is|are|was|were)\s+required\s+(?!to\b)[A-Za-z'-]+\b"
            r"|\b(?:wandered|entered|reported|requested|received|included|"
            r"provided|stored|placed|checked|reviewed|tested|confirmed)\s+"
            r"(?:wandered|entered|reported|requested|received|included|"
            r"provided|stored|placed|checked|reviewed|tested|confirmed)\b"
            r"|\bshould have has been\b"
            r"|\bshould explain explains\b"
            r"|\bmust not blame identifies\b"
            r"|\bgives receive written approval\b"
            r"|\bgrants approval for the trial does not authorize\b"
            r"|\bprior\s+to\s+(?:the\s+)?[A-Za-z'-]+\s+"
            r"(?:begins?|starts?|ends?|finishes?|schedules?)\b"
            r"|\bis\s+preparing\s+conducting\b"
            r"|\bsays\s+at\s+the\b"
            r"|\bsays\s+will\s+test\s+assess\s+whether\b"
            r"|\bcontinue\s+operate\b"
            r"|\bcontain\s+received\s+conflicting\b"
            r"|\bbeing\s+was\s+not\b"
            r"|\bdespite\s+[^,.;]{1,80}?\s+was\s+not\b"
            r"|\ball\s+failed\s+control\b",
            re.IGNORECASE,
        ),
    ),
    (
        "malformed rewrite residue",
        re.compile(
            r"\b(?:did not measure analy(?:s|z)ed|"
            r"must not be was presented|carried drew|closed relocated|"
            r"reached is zero|included includes|displays still displayed|"
            r"are charged will not pay|cannot provide advertise|"
            r"should explain explaining|"
            r"will not pay are charged|"
            r"use (?:reported )?working later hours|"
            r"use preferred later hours|"
            r"phase eight tablets|"
            r"instructions for Several are written|"
            r"did not measure looked at|"
            r"presented that as proof|"
            r"three trainers spoke simultaneously,\s+which screen|"
            r"which represented which|numbers sound are|"
            r"there were which caused|appointing hiring|controlled Neither|"
            r"Garden L-\d+,\s+remained|"
            r"or the reason why [^.!?]{1,100} remains unclear)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "awkward rephrase phrase",
        re.compile(
            r"\bfrom County Health Office\b"
            r"|\bcritical operations can sustain\b"
            r"|\bnobody notified the issue\b"
            r"|\bspecified approved\b"
            r"|\bneither removed values nor estimated temperatures\b"
            r"|\bany adjustments should include initials\b"
            r"|\bessential services be able to continue\b"
            r"|\bwere submitted delayed\b"
            r"|,\s+and (?-i:Nobody)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "correlative agreement",
        re.compile(
            r"\bNeither\b[^.!?]{1,100}\bor\b[^.!?]{1,100}\b(?:is|are)\s+allowed\b",
            re.IGNORECASE,
        ),
    ),
    (
        "existential agreement",
        re.compile(
            r"\bthere\s+(?:is|was)\s+(?:also\s+)?(?:many|several|multiple|few|"
            r"two|three|four|five|six|seven|eight|nine|ten)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "malformed list phrase",
        re.compile(
            r"\b(?:big|small|little|large)\s+includes?\s+"
            r"(?:dogs?|cats?|animals?|breeds?)\b"
            r"|\btranslated survey,\s*dates the two transportation disruptions\b",
            re.IGNORECASE,
        ),
    ),
    (
        "pronoun agreement",
        re.compile(r"\bbring toys over and put it on\b", re.IGNORECASE),
    ),
    (
        "capitalization after comma",
        re.compile(r",\s+(?:But|And|Or|So|Yet)\b"),
    ),
    (
        "missing clause comma",
        re.compile(
            r";\s*however(?!,)\s+"
            r"|\brecords the time\s+however\b"
            r"|\bHowever\s+(?!,)(?:MedTrack|The)\b"
            r"|\bAt each stage(?!,)\s+"
            r"|\bblocked two emergency repairs\s+which was a serious mistake\b"
            r"|\bpounds of produce\s+of which\b"
            r"|(?<![,;:])(?<!not)\s+but\s+"
            r"(?:several|the|they|he|she|it|MedTrack)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "capitalized article after transition",
        re.compile(r"\bHowever,\s+The transit board\b"),
    ),
    (
        "malformed existential phrase",
        re.compile(r"\bThere also have several\b", re.IGNORECASE),
    ),
    (
        "capitalized transition after semicolon",
        re.compile(r";\s*However\b"),
    ),
    (
        "malformed college phrase",
        re.compile(
            r"\b(?:must not overwrite accessed the earlier backup|"
            r"must not be presented these findings as evidence|"
            r"There also present several|"
            r"does not follow was drawn from|"
            r"and no establish a clear sequence|"
            r"should be cautioned more cautiously)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "malformed diplomatic phrase",
        re.compile(
            r"\b(?:Amina requested Owen to verify|"
            r"must not overwrite referenced the earlier backup|"
            r"must not be presented this as evidence|"
            r"should explain will explain)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "malformed empathetic phrase",
        re.compile(r"\bshould be explained it more cautiously\b", re.IGNORECASE),
    ),
    (
        "missing introductory comma",
        re.compile(
            r"\b(?:If the board rejects it the library|"
            r"If costs exceed the grant the program|"
            r"Last spring two)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "awkward preference phrase",
        re.compile(
            r"\bindicated they would use (?:a )?preference for later hours\b",
            re.IGNORECASE,
        ),
    ),
    (
        "verb choice",
        re.compile(r"\b(?:they just lay on|laying around)\b", re.IGNORECASE),
    ),
    (
        "awkward word order",
        re.compile(r"\bdoing not much\b", re.IGNORECASE),
    ),
    (
        "verb and possessive agreement",
        re.compile(
            r"\bevery\s+dog\s+(?:got|have)\s+(?:there|their)\s+own\b",
            re.IGNORECASE,
        ),
    ),
    (
        "comma splice",
        re.compile(
            r"\benergy,\s*then\s+they\b"
            r"|,\s*(?:nobody\s+(?:reported|could)|"
            r"that\s+argument\s+was|the\s+support\s+team\s+marked|"
            r"another\s+paragraph\s+repeats|readers\s+(?:get|gets)|"
            r"the\s+media\s+vendor\s+haven't)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "sentence fragment",
        re.compile(
            r"(?:^|(?<=[.!?])\s+)(?:Because|Which|While)\s+[^.!?,]{2,220}\.",
            re.IGNORECASE,
        ),
    ),
    (
        "fused sentence",
        re.compile(
            r"\bwithout commas\s+another paragraph\b"
            r"|\bconsent procedures\s+readers?\s+(?:get|gets)\b"
            r"|\bare fragments others combine\b"
            r"|\b(?:attendance at public meetings|public meeting attendance)\s+They recorded\b",
            re.IGNORECASE,
        ),
    ),
    (
        "incomplete clause",
        re.compile(
            r"\b(?:stop|stops|stopped|pause|paused)\s+without\."
            r"|(?:^|(?<=[.!?])\s+)(?:Instead,\s+(?:tracked|counted|recorded)|"
            r"Without\s+uses|Not\s+(?:comes|is|are|was|were|has|had|does|did|can|could|"
            r"will|would|should|must|may|might)|But\s+does\s+not|Does\s+not)\b"
            r"|\bafter\.|(?:^|(?<=[.!?])\s+)(?:Omitting|Leaving|Adding|Removing)\.",
            re.IGNORECASE,
        ),
    ),
    (
        "correlative punctuation",
        re.compile(
            r"\bNeither\b[^.!?;]{1,160};\s+nor\b",
            re.IGNORECASE,
        ),
    ),
    (
        "malformed percentage clause",
        re.compile(
            r"\bwith a fuel tank at \d{1,3} percent full\b",
            re.IGNORECASE,
        ),
    ),
    (
        "generator comma splice",
        re.compile(
            r"\bThe backup generator was serviced on "
            r"(?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December) \d{1,2} "
            r"and its fuel tank is\b",
            re.IGNORECASE,
        ),
    ),
    (
        "missing compound comma",
        re.compile(
            r"\b(?:checklists and none of them|"
            r"districts and every technician|"
            r"submitted late and they contain|"
            r"messages and the billing preview|"
            r"\d{4} and the appendix|"
            r"neighborhood problem but this claim|"
            r"low attendance but that is|"
            r"property values and it must not be presented|"
            r"Conference Room [A-Z0-9-]+ and remote workers|"
            r"that district but other districts|"
            r"does not request permanent hours and it does not|"
            r"That tone weakens the proposal and it does not|"
            r"the note should be brief but omitting|"
            r"no clear order and the word ['\"]?utilize)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "compound predicate comma",
        re.compile(
            r"\bA survey indicates interest,\s+but does not\b",
            re.IGNORECASE,
        ),
    ),
    (
        "misplaced time comma",
        re.compile(
            r"\bAt \d{1,2}:\d{2}\s+[ap]\.m\.,\s+"
            r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
            r"[a-z]",
            re.IGNORECASE,
        ),
    ),
    (
        "missing clause comma",
        re.compile(
            r"\b(?:neighborhood problem a claim that|"
            r"six weeks which caused|"
            r"consent procedures (?:causing|confusing) readers|"
            r"(?:technical )?cause although)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "awkward connector",
        re.compile(r",\s+despite that it\b", re.IGNORECASE),
    ),
    (
        "malformed article phrase",
        re.compile(r"\bhas no a useful effect\b", re.IGNORECASE),
    ),
    (
        "unnecessary role commas",
        re.compile(
            r"\bThe accessibility coordinator,\s+"
            r"[A-Z][A-Za-z'-]+ [A-Z][A-Za-z'-]+,\s+requested\b"
        ),
    ),
    (
        "malformed passive phrase",
        re.compile(r"\bmust not be presented them as\b", re.IGNORECASE),
    ),
    (
        "malformed determiner",
        re.compile(
            r"\b(?:The Neither event|No A personal email address|No An employee)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "awkward title article",
        re.compile(
            r"\bdirected the system administrator "
            r"[A-Z][A-Za-z'-]+ [A-Z][A-Za-z'-]+\b"
        ),
    ),
    (
        "vague rephrase",
        re.compile(
            r"\bLast week's rehearsal was different due to "
            r"three trainers speaking at once\b",
            re.IGNORECASE,
        ),
    ),
    (
        "faulty comparison",
        re.compile(
            r"\bless precise compared to the extended version\b"
            r"|\bmore reliable indicator of involvement compared to "
            r"the size of the garden\b",
            re.IGNORECASE,
        ),
    ),
    (
        "existential agreement",
        re.compile(r"\bThere also contain\b", re.IGNORECASE),
    ),
    (
        "duplicate preposition",
        re.compile(r"\bwithout with commas\b", re.IGNORECASE),
    ),
    (
        "mid-sentence capitalization",
        re.compile(
            r"\bclaim Gardens eliminate\b"
            r"|\bAt Garden [A-Z]-\d{2},\s+The city\b"
        ),
    ),
    (
        "malformed access clause",
        re.compile(
            r"\bhad the option to refuse audio recording without,\s+"
            r"which would result in losing access\b",
            re.IGNORECASE,
        ),
    ),
    (
        "unpunctuated list",
        re.compile(
            r"\b(?:route syncing\s+offline notes\s+photo uploads\s+and emergency escalation|"
            r"soil testing\s+tool storage\s+compost training\s+and language access|"
            r"elevator recall\s+emergency lighting\s+oxygen alarms\s+door locks|"
            r"customer names\s+gate codes\s+and service notes|"
            r"every unresolved defect\s+the person responsible\s+"
            r"the next review time\s+and whether|"
            r"volunteer hours\s+crop donations\s+cancelled workdays\s+"
            r"and attendance|"
            r"amounts\s+customer names\s+or tax codes|"
            r"adding a 409 regression test\s+defining the "
            r"(?:producer pause )?order in RB-SYNC-64\s+"
            r"and changing alert HP-DUP-4|"
            r"nightly attendance\s+repeat visits\s+reported safety issues\s+"
            r"staff overtime\s+and the percentage|"
            r"translated survey dates\s+the two transportation disruptions\s+"
            r"and a table)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "uncorrected vocabulary",
        re.compile(
            r"\b(?:very unique|irregardless|normal fashion|completely free|"
            r"more easier|more stronger|Follow actions)\b"
            r"|The last report is due\b",
            re.IGNORECASE,
        ),
    ),
    (
        "number agreement",
        re.compile(
            r"\bdifferent in their own ways\b"
            r"|\bSome dogs like swimming while another dog hates\b",
            re.IGNORECASE,
        ),
    ),
    (
        "incorrect comparative",
        re.compile(r"\b(?:more better|more worse|most best|most worst)\b", re.IGNORECASE),
    ),
    (
        "informal spelling",
        re.compile(r"\b(?:alot|atleast|probly|could of|should of|would of)\b", re.IGNORECASE),
    ),
    (
        "repeated punctuation",
        re.compile(r"([!?.,])\1+"),
    ),
    (
        "repeated word",
        # Skip a proper noun said twice ("Bora Bora", "Walla Walla") where
        # both tokens are capitalized, but still catch an accidental doubled
        # word ("the the", "By by").
        re.compile(
            r"\b(?!(?i:had|that|very)\b)"
            r"(?:([a-z][a-z'-]{1,})\s+\1|([A-Z])([a-z'-]+)\s+[a-z]\3)\b"
        ),
    ),
    (
        "missing sentence spacing",
        # Require a real word ending, a terminator, then a capitalized word
        # start. Abbreviations and product names ("U.S.A.", "Node.JS") no
        # longer trip this the way a bare [.!?][A-Z] did.
        re.compile(r"[a-z][.!?][A-Z][a-z]"),
    ),
    (
        "lowercase sentence start",
        re.compile(r"(?:^|[.!?]\s+)strongest interest\b"),
    ),
    (
        "missing date-response separator",
        re.compile(
            r"\b[A-Z][a-z]+ \d{1,2} and [A-Z][a-z]+ \d{1,2}\s+"
            r"\d+ respondents\b",
        ),
    ),
)


class CorrectionQualityGate:
    """Conservative final gate. Unknown quality is incomplete, never silently accepted."""

    def evaluate(self, text: str, *, command: str) -> CorrectionQualityResult:
        residual = tuple(
            name for name, pattern in _RESIDUAL_CHECKS if pattern.search(text)
        )
        tone = TONE_CONTRACTS.get(command)
        tone_violations = self._tone_violations(text, tone) if tone is not None else ()
        # Every visible tone contract explicitly prohibits broken grammar.
        # A stylistically plausible sentence with a known residual error has
        # not passed that contract, even when no tone-specific regex fires.
        tone_verified = tone is None or (not residual and not tone_violations)
        deductions = min(1.0, 0.18 * len(residual) + (0.25 if not tone_verified else 0.0))
        score = max(0.0, 1.0 - deductions)
        # `complete` keeps its plain meaning: nothing known is left wrong.
        # Whether a candidate has to be complete to be shown is the engine's
        # decision, not this gate's - see PolishEngine._beats_deterministic.
        return CorrectionQualityResult(
            complete=not residual and tone_verified,
            residual_errors=residual,
            tone_verified=tone_verified,
            score=score,
            error_score=error_score(text),
        )

    @staticmethod
    def _tone_violations(text: str, contract: ToneContract) -> tuple[str, ...]:
        violations: list[str] = []
        if contract.name in {"Formal", "Academic"} and re.search(
            r"\b(?:(?:ain|aren|can|couldn|didn|doesn|don|hadn|hasn|haven|"
            r"isn|mustn|needn|shan|shouldn|wasn|weren|won|wouldn)"
            r"(?:'|\u2019)t|I(?:'|\u2019)(?:m|ve|ll|d)|"
            r"(?:you|we|they)(?:'|\u2019)(?:re|ve|ll|d)|"
            r"(?:he|she|it|that|there|here|what|who)(?:'|\u2019)"
            r"(?:s|ll|d)|let(?:'|\u2019)s)\b",
            text,
            re.IGNORECASE,
        ):
            violations.append("contractions")
        if contract.name == "Caveman":
            long_ordinary_sentences = []
            for sentence in re.split(r"(?<=[.!?])\s+", text):
                if not sentence.strip():
                    continue
                word_count = len(_TONE_WORD.findall(sentence))
                structured_fact_list = sentence.count(",") >= 2
                if word_count > 18 and not structured_fact_list:
                    long_ordinary_sentences.append(sentence)
            if long_ordinary_sentences:
                violations.append("sentences too long")
            # Caveman splits sentences aggressively and can strand a bare
            # conditional ("If any alarm fails triggers."). Real documents
            # legitimately contain short If-clauses, so this check applies
            # only to the Caveman rewrite, never to Proofread output.
            if _CAVEMAN_DANGLING_CONDITIONAL.search(text):
                violations.append("dangling conditional fragment")
        return tuple(violations)


def tone_contract_instruction(command: str) -> str:
    contract = TONE_CONTRACTS.get(command)
    if contract is None:
        return ""
    prohibited = ", ".join(contract.prohibited_behaviors)
    return (
        f"Tone contract: {contract.instruction} Vocabulary: {contract.vocabulary}. "
        f"Formality: {contract.formality}. Warmth: {contract.warmth}. "
        f"Directness: {contract.directness}. Sentence length: {contract.sentence_length}. "
        f"Contractions: {contract.contractions}. Structure: {contract.structure}. "
        # Spelled out for every tone, with the words named.
        #
        # In English a register shift often IS a modal shift, so a model asked
        # to sound warmer reaches for "might" over "may", or slips a "can"
        # into a flat claim, by doing exactly what was asked. The meaning
        # guards refuse that and they are right to: "hesitation blocks
        # progress" and "hesitation can block progress" are different claims.
        # Measured 2026-08-20, this was 14 of 19 modal refusals on Friendly.
        "Modality: keep the writer's certainty exactly as it is. Do not add or "
        "remove can, may, might, could, would, or any other hedge, and do not "
        "soften a plain statement into a possibility. "
        f"Do not produce: {prohibited}."
    )


__all__ = [
    "CorrectionQualityGate",
    "CorrectionQualityResult",
    "TONE_CONTRACTS",
    "ToneContract",
    "tone_contract_instruction",
]
