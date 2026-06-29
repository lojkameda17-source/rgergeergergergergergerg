"""
Fast in-memory keyword matcher — zero DB calls per message.
Supports exact, prefix and phrase (word-order) matching.
"""
import re
from dataclasses import dataclass, field


def _norm(text: str) -> str:
    t = text.lower()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _word_eq(a: str, b: str) -> bool:
    """Soft word equality: exact OR common 5-char prefix for words >= 5 chars."""
    if a == b:
        return True
    if len(a) >= 5 and len(b) >= 5:
        return a[:5] == b[:5]
    return False


def _phrase_matches(phrase_tokens: list[str], msg_tokens: list[str]) -> bool:
    pos = 0
    for pt in phrase_tokens:
        found = False
        while pos < len(msg_tokens):
            if _word_eq(pt, msg_tokens[pos]):
                pos += 1
                found = True
                break
            pos += 1
        if not found:
            return False
    return True


@dataclass
class Matcher:
    singles: set[str] = field(default_factory=set)
    phrases: list[tuple[str, list[str]]] = field(default_factory=list)  # (original_kw, tokens)

    def is_empty(self) -> bool:
        return not self.singles and not self.phrases

    def match(self, raw: str, mode: str) -> str | None:
        """Returns matched keyword or None. mode: 'mixed' | 'phrase'"""
        norm = _norm(raw)
        tokens = norm.split()
        token_set = set(tokens)

        # phrases always checked
        for orig, ph_tok in self.phrases:
            if _phrase_matches(ph_tok, tokens):
                return orig

        if mode == "phrase":
            return None

        # single-word: exact match
        for kw in self.singles:
            if kw in token_set:
                return kw

        # single-word: prefix match
        for kw in self.singles:
            if len(kw) >= 5:
                for tok in tokens:
                    if len(tok) >= 5 and tok[:5] == kw[:5]:
                        return kw

        return None


def build_matcher(rows: list) -> Matcher:
    singles: set[str] = set()
    phrases: list[tuple[str, list[str]]] = []
    for row in rows:
        kw = _norm(row.value or "")
        if not kw:
            continue
        toks = kw.split()
        if len(toks) == 1:
            singles.add(kw)
        else:
            phrases.append((kw, toks))
    # longer phrases checked first (more specific)
    phrases.sort(key=lambda x: len(x[1]), reverse=True)
    return Matcher(singles=singles, phrases=phrases)
