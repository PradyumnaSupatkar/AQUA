"""Task-local consent for read-only UI metadata, never action routing.

Qwen chooses the observation tools. This module only bounds which approved
applications the original user request permits those tools to observe. Window
references and application-provided text cannot grant consent here.
"""

from dataclasses import dataclass
import re

from aqua.actions.launcher import ALLOWED_APPS, APP_ALIASES

MAX_REQUEST_CHARACTERS = 4000


@dataclass(frozen=True, slots=True)
class ObservationPermission:
    status: str
    apps: tuple[str, ...]
    message: str
    requires_inspection: bool = True


_UI = r"(?:windows?|interfaces?|controls?|buttons?|menus?|tabs?|dialogs?|ui|desktop|screen|accessibility(?:\s+tree)?)"
_STATE = r"(?:open|visible|active|enabled|disabled|selected|displayed|shown|present|closed|minimized|maximized|there)"
_INVENTORY_INTENT = (
    r"\b(?:what|which)\s+(?:applications?|apps?|windows?)\s+"
    r"(?:(?:can|do)\s+you\s+(?:currently\s+)?see(?:\s+open)?|"
    r"are\s+(?:currently\s+)?(?:open|visible|active)(?:\s+(?:now|currently))?)(?![-\w])"
)
_INTENT = re.compile(
    _INVENTORY_INTENT + r"|"
    r"\b(?:inspect(?:ing)?|observ(?:e|ing)|examin(?:e|ing))\b|\blook\s+(?:at|inside)\b|"
    r"\bwhat\s+(?:can|do)\s+you\s+(?:currently\s+)?(?:see|observe)\b|"
    r"\bwhat\s+you\s+can\s+see\b|"
    r"\b(?:can|could|would|will)\s+you\s+see\b|"
    r"\bwhat(?:'s|\s+is)\s+(?:inside|in|on|visible\s+in)\b|"
    r"\b(?:list|enumerate|show|describe|summari[sz]e|read)\b(?=[^.!?;]*\b" + _UI + r"\b)|"
    # Merely mentioning Windows, buttons or menus in a factual question is
    # not desktop consent. These branches require a current-state predicate
    # or an explicit location such as "in my ... window".
    r"\b(?:what|which)\b(?=[^.!?;]*\b" + _UI + r"\b)"
    r"(?=[^.!?;]*\b(?:is|are)\s+(?:currently\s+)?" + _STATE + r"(?:\s+(?:now|currently))?\s*$)|"
    r"\b(?:what|which)\b(?=[^.!?;]*\b" + _UI + r"\b)"
    r"(?=[^.!?;]*\b(?:is|are)\s+(?:in|inside|on)\s+(?:my|the|this|your)\b)|"
    r"\b(?:is|are)\b(?=[^.!?;]*\b" + _UI + r"\b)"
    r"(?=[^.!?;]*\b" + _STATE + r"(?:\s+(?:now|currently))?\s*$)|"
    r"\b(?:is|are)\b(?=[^.!?;]*\b" + _STATE + r"(?:\s+(?:now|currently))?\s*$)",
)
_CONSENT_PREFIX = re.compile(
    r"(?:^|\b(?:and|then|also|but)\s+)"
    r"(?:(?:aqua|hey\s+aqua|hello\s+aqua|well|actually)[\s,]+)?"
    r"(?:(?:can|could|would|will)\s+you\s+|"
    r"i\s+(?:(?:would\s+)?like|want|need)\s+you\s+to\s+|i'd\s+like\s+you\s+to\s+|tell\s+me\s+)?"
    r"(?:please\s+)?$",
)
_OPERATION = r"(?:open|launch|start|run|execute|click|double-click|type|press|write|edit|save|send|delete|erase|remove|move|copy|rename|download|upload|install|close|switch|focus|scroll|paste|select|drag|activate|maximize|minimize|resize|capture|record|screenshot|take)"
_EFFECT_REQUEST = re.compile(
    r"(?:^|\b(?:and|then|also|to|after\s+that)\s+)"
    r"(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?" + _OPERATION + r"\b|"
    r"\bi\s+(?:want|need|would\s+like)\s+you\s+to\s+" + _OPERATION + r"\b",
)
_NEGATED_OBSERVATION = re.compile(
    r"\b(?:do\s+not|don't|dont|never|avoid|stop|should\s+not|shouldn't|must\s+not|mustn't|no\s+need\s+to)"
    r"\s+(?:\w+\s+){0,4}(?:inspect|observ\w*|examin\w*|look|see|read|list|show)\b",
)
_EXPLANATION = re.compile(r"\b(?:how\s+to|how\s+do\s+i|how\s+would|what\s+would|explain\s+how|tell\s+me\s+about)\b")
_FILLER = frozenset(
    "my the a an windows window interface interfaces controls control buttons button menus menu "
    "tabs tab dialogs dialog ui accessible accessibility tree trees application applications app "
    "apps in on of for from within inside currently current active visible top level and plus as "
    "well please me now all that which is are open opened see can you your what look at supported "
    "approved s screen screens desktop enabled disabled selected displayed shown present closed minimized maximized there".split()
)


def _names():
    names = {name: app for app in ALLOWED_APPS for name in (app,)}
    names.update({alias: app for alias, app in APP_ALIASES.items() if app in ALLOWED_APPS})
    for alias, app in (("browser", "chrome"), ("web browser", "chrome"),
                       ("coding environment", "vs code"), ("code editor", "vs code"),
                       ("development environment", "vs code"), ("text editor", "notepad")):
        if app in ALLOWED_APPS:
            names[alias] = app
    return names


def _permission(status, message, apps=(), *, requires_inspection=True):
    return ObservationPermission(status, tuple(app for app in ALLOWED_APPS if app in apps), message,
                                 requires_inspection=requires_inspection)


def _needs_control_inspection(clause, intent):
    """Describe the granted observation goal, without choosing any tool step."""
    request = clause[intent.start():]
    if re.search(r"\b(?:interfaces?|controls?|buttons?|menus?|tabs?|dialogs?|ui|accessibility|trees?)\b", request):
        return True
    inventory = re.match(_INVENTORY_INTENT, request) is not None
    metadata_query = inventory or intent.group() in {"list", "enumerate", "show", "what", "which", "is", "are"}
    return not (metadata_query and (inventory or intent.group() in {"is", "are"} or re.search(r"\bwindows?\b", request)))


def observation_permission(text) -> ObservationPermission:
    """Derive fresh UI-metadata consent from one original user utterance.

    The conservative English consent grammar may require a clearer request.
    It never grants observation merely because an app is in the launcher list.
    """
    if type(text) is not str or not text.strip() or len(text) > MAX_REQUEST_CHARACTERS:
        return _permission("denied", "Please make a bounded, explicit interface-observation request.")
    lower = text.casefold().replace("\u2019", "'").replace("\u2018", "'")
    clauses = [clause.strip() for clause in re.split(r"[.!?;\r\n]+", lower) if clause.strip()]
    requested = [(clause, _INTENT.search(clause)) for clause in clauses]
    requested = [(clause, match) for clause, match in requested if match is not None]
    sensitive = re.search(r"\b(?:read|extract|reveal|show)\b[^.!?;]*\b(?:passwords?|secrets?|clipboard|document\s+(?:text|contents?)|file\s+contents?)\b", lower)
    if sensitive and (requested or any(re.search(r"\b" + re.escape(name) + r"\b", lower) for name in _names())):
        return _permission("denied", "Observation does not extract passwords, clipboard data, or document contents.")
    if not requested:
        if (re.match(r"(?:(?:hey\s+)?aqua[\s,]+)?(?:what|which)\b", lower)
                and re.search(r"\b(?:currently|now|open|visible|active|running)\b", lower)
                and re.search(r"\b(?:desktop|screen)\b", lower)):
            return _permission("clarification", "Current desktop facts require a fresh scoped observation. Ask which applications are open on your desktop, or name an approved application's window.")
        return _permission("none", "No explicit interface-observation request was made.")
    if re.search(r"\b(?:ignore|override|bypass)\b[^.!?;]*\b(?:rules|instructions|schema|system|prompt|policy)\b", lower):
        return _permission("denied", "Observation cannot bypass authorization or safety restrictions.")
    if re.search(r"\b(?:my\s+friend\s+said|the\s+message\s+says|the\s+phrase|quoted\s+instruction)\b", lower):
        return _permission("denied", "Reported instructions are not observation permission.")
    for quote in re.finditer(r'"([^"\r\n]+)"|\u201c([^\u201d\r\n]+)\u201d|`([^`\r\n]+)`|(?<!\w)\'([^\'\r\n]+)\'(?!\w)', lower):
        if _INTENT.search(next(group for group in quote.groups() if group is not None)):
            return _permission("denied", "Quoted instructions are not observation permission.")
    if any(_EFFECT_REQUEST.search(clause) for clause in clauses):
        return _permission("denied", "Observation requests must be read-only; mixed effect requests are not approved.")
    if re.search(r"[a-z]:[\\/]|\\|(?:^|\s)/[\w.~%/-]+|\$\(|[|&]", lower):
        return _permission("denied", "Paths and shell syntax are outside interface-observation scope.")
    names = _names()
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True)) + r")\b")
    scope = set()
    requires_inspection = False
    for clause, intent in requested:
        if _NEGATED_OBSERVATION.search(clause):
            return _permission("denied", "A negated request cannot grant observation permission.")
        if re.search(r"\b(?:suppose|imagine|pretend|hypothetical|hypothetically)\b", clause):
            return _permission("denied", "Hypothetical observation is not immediate permission.")
        if re.search(r"\b(?:if|unless|when|once|until)\b", clause):
            return _permission("denied", "Please request immediate, unconditional interface observation.")
        if _EXPLANATION.search(clause[:intent.end()]):
            continue
        if not _CONSENT_PREFIX.search(clause[:intent.start()]):
            continue  # Describing one's own inspection is not delegating it.
        target = clause[intent.end():]
        # Incidental preceding app mentions are outside this target. A trailing
        # discussion or simultaneous activity is not another requested app.
        target = re.split(r"\b(?:while|because|since|although|whereas|but|and\s+(?:tell|explain|describe))\b", target, maxsplit=1)[0]
        apps = {names[match.group()] for match in pattern.finditer(target)}
        remainder = pattern.sub(" ", target)
        words = re.findall(r"[a-z0-9_-]+", remainder)
        if any(word not in _FILLER for word in words):
            return _permission("denied", "Please name only approved applications and their interfaces; no partial observation scope was granted.")
        if not apps:
            if (re.match(_INVENTORY_INTENT, clause[intent.start():])
                    and re.search(r"\bon\s+(?:my|the|this|your)\s+(?:desktop|screen)\b", target)):
                apps = set(ALLOWED_APPS)  # Metadata of approved visible windows only.
            else:
                return _permission("clarification", "Which approved application's interface may I observe? Please name Notepad, Calculator, VS Code, or Chrome.")
        scope.update(apps)
        requires_inspection = requires_inspection or _needs_control_inspection(clause, intent)
    if not scope:
        return _permission("none", "An explanation or description of observation does not grant inspection consent.")
    return _permission("approved", "Read-only interface metadata for the named approved applications is authorized for this task only.",
                       scope, requires_inspection=requires_inspection)
