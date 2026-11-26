"""Host checks every memory must pass, whatever the model proposed.

1. It comes from the user's own words in this request: its content words appear in the request
   text, never only in tool results, page text (YouTube titles), window titles or earlier turns.
2. It is not a secret (passwords, PINs, keys, tokens, card or account numbers).
3. It is not about a sensitive personal category (health, finances, religion, sexuality,
   politics, ethnicity, criminal record, immigration status).
4. It is not text the user dictated into an app this request.
"""

import re

_SECRET = re.compile(
    r"\b(?:pass\s?words?|passcodes?|passphrases?|pins?|pin\s+codes?|api\s+keys?|keys?\s+(?:is|are)|secret|secrets|"
    r"tokens?|credentials?|log\s?ins?|otp|one[-\s]time\s+codes?|security\s+codes?|cvv|cvc|recovery\s+codes?|"
    r"seed\s+phrases?|private\s+keys?|ssh\s+keys?|social\s+security|ssn|aadhaar|pan\s+(?:card|number)|"
    r"passport\s+(?:number|no)|(?:credit|debit|bank)\s+card|card\s+number|account\s+number|iban|routing\s+number)\b|"
    r"\b(?:sk|pk|ghp|xox[abp])[-_][A-Za-z0-9]{8,}|\b[A-Za-z0-9+/]{32,}={0,2}(?=\s|$)|\b(?:\d[\s-]?){12,19}\b", re.I)
_SENSITIVE = re.compile(
    r"\b(?:health|medical|medicines?|medications?|diagnos(?:is|ed)|disease|illness|sick|diabet\w*|cancer|hiv|aids|"
    r"asthma|allerg\w*|depress\w*|anxiety|adhd|autis\w*|bipolar|schizo\w*|therap(?:y|ist)|psychiatr\w*|pregnan\w*|"
    r"disabilit\w*|surgery|blood\s+(?:type|pressure)|prescriptions?|"
    r"salary|income|earn(?:s|ings)?|debts?|loans?|mortgage|credit\s+score|bank\s+balance|savings|net\s+worth|taxes|"
    r"bankrupt\w*|religio\w*|church|mosque|temple|muslim|christian|hindu|jewish|sikh|buddhist|atheist|"
    r"gay|lesbian|bisexual|transgender|sexual\w*|political\s+(?:party|views?)|vote[sd]?\s+for|democrat|republican|"
    r"ethnicity|race|caste|criminal\s+record|arrested|convicted|immigration|visa\s+status|undocumented)\b", re.I)
_WORD = re.compile(r"[a-z0-9]+(?:['’][a-z]+)?")
_FILLER = frozenset("""a an the and or to of in on at for with by is are was were be am i me my mine you your it its
that this from now always never please remember aqua user users user's that's i'm i've s""".split())


def secret(text: str) -> bool:
    return bool(_SECRET.search(text))


def sensitive(text: str) -> bool:
    return bool(_SENSITIVE.search(text))


def _words(text: str) -> list[str]:
    return [w.replace("’", "'") for w in _WORD.findall(text.casefold())]


def grounded(text: str, request: str) -> bool:
    """Every content word of the memory (allowing small changes like "I"/"my" -> "your", a
    plural or a tense) is in the user's request. At least one content word must be."""
    said = _words(request)
    content = [w for w in _words(text) if w not in _FILLER and w not in {"your", "you're", "they", "their"}]
    if not content:
        return False

    def in_request(word: str) -> bool:
        return any(w == word or (len(word) >= 4 and len(w) >= 4 and (w.startswith(word[:-1]) or word.startswith(w[:-1])))
                   for w in said)
    return all(in_request(word) for word in content)


def refusal(text: str, request: str, dictated: tuple[str, ...] = ()) -> str | None:
    """Why this memory may not be saved, or None when it may."""
    if secret(text) or secret(request):
        return "Aqua never remembers passwords, PINs, keys or account numbers."
    if sensitive(text):
        return "Aqua does not remember health, money or other sensitive personal details."
    folded = " ".join(text.casefold().split())
    if any(folded and folded in " ".join(d.casefold().split()) for d in dictated if d):
        return "That is text you dictated into an app, not something to remember."
    if not grounded(text, request):
        return ("Only what the user says in this request can be remembered, not text from tools, pages or "
                "window titles.")
    return None
