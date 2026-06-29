"""
Fast in-memory keyword matcher — zero DB calls per message.
Supports strict phrase matching, mixed fuzzy matching and offer-post filtering.
"""
import re
from dataclasses import dataclass, field


_OFFER_PATTERNS = [
    r"\bпредлага(ю|ем|ет|ют)\b",
    r"\bпредставляем\b",
    r"\bоказыва(ю|ем|ет|ют)\b",
    r"\bпомож(ем|ет|ете)\b",
    r"\bсдела(ю|ем|ет|ют)\b",
    r"\bразработ(ка|аем|аю|чик|чики)\b",
    r"\bнаши\s+(услуги|сервисы|софты)\b",
    r"\bнаш\s+(сервис|софт|бот|продукт)\b",
    r"\bплатформа\b",
    r"\bваканси(я|и)\b",
    r"\bворк\s+из\s+дома\b",
    r"\bищ(у|ем)\s+(людей|сотрудников|работников|арбитражников|менеджеров)\b",
    r"\bвсему\s+обуч(им|аем)\b",
    r"\bкому\s+интересно\b",
    r"\bписать\s+в\s+лс\b",
    r"@\w{4,}",
]

_SEEKER_PATTERNS = [
    r"\bищ(у|ем)\s+(сервис|услугу|подрядчика|исполнителя|специалиста|человека|арбитражника|рассылк|сайт|бота|парсер)\b",
    r"\bнуж(ен|на|ны|но)\s+(сервис|услуга|подрядчик|исполнитель|специалист|человек|арбитражник|рассылка|сайт|бот|парсер)\b",
    r"\bкто\s+(занимается|делает|может|умеет)\b",
    r"\bкак\s+(сделать|запустить|настроить|найти)\b",
    r"\bпосоветуйте\b",
]


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


def _phrase_matches_fuzzy(phrase_tokens: list[str], msg_tokens: list[str]) -> bool:
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


def _phrase_matches_strict(phrase_tokens: list[str], msg_tokens: list[str]) -> bool:
    if not phrase_tokens or len(phrase_tokens) > len(msg_tokens):
        return False
    width = len(phrase_tokens)
    return any(msg_tokens[i:i + width] == phrase_tokens for i in range(len(msg_tokens) - width + 1))


def looks_like_offer_post(raw: str) -> bool:
    """Best-effort guard against ads/job posts from people offering services, not seeking them."""
    text = raw.lower()
    offer_score = sum(1 for pattern in _OFFER_PATTERNS if re.search(pattern, text, flags=re.UNICODE))
    seeker_score = sum(1 for pattern in _SEEKER_PATTERNS if re.search(pattern, text, flags=re.UNICODE))
    return offer_score >= 2 and offer_score > seeker_score


@dataclass
class Matcher:
    singles: set[str] = field(default_factory=set)
    phrases: list[tuple[str, list[str]]] = field(default_factory=list)  # (original_kw, tokens)

    def is_empty(self) -> bool:
        return not self.singles and not self.phrases

    def match(self, raw: str, mode: str) -> str | None:
        """Returns matched keyword or None. mode: 'mixed' | 'phrase'.

        In phrase mode phrases must be exact consecutive normalized words. This
        prevents a keyword like "ищу арбитражника" from matching a long ad that
        only contains "ищу" and a distant word with the same prefix as
        "арбитражника".
        """
        norm = _norm(raw)
        tokens = norm.split()
        token_set = set(tokens)

        if mode == "phrase":
            for orig, ph_tok in self.phrases:
                if _phrase_matches_strict(ph_tok, tokens):
                    return orig
            return None

        # mixed mode keeps fuzzy phrase matching for broader discovery.
        for orig, ph_tok in self.phrases:
            if _phrase_matches_fuzzy(ph_tok, tokens):
                return orig

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
