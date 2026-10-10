"""Text embeddings for knowledge retrieval.

``hashing`` (default, no downloads): TF-IDF weighted feature hashing over words, word
bigrams and character trigrams, with English/Hindi/Hinglish stopwords and a small
synonym map (e.g. "chakkar" → dizziness, "sujan" → swelling). Fitted on the corpus so
rare, meaningful words outweigh generic ones like "pregnancy".

``hybrid`` joins those keyword vectors with semantic vectors from an Ollama embedding model
(``nomic-embed-text``), so questions worded differently from the documents still find them.
``auto`` (default) uses hybrid when that model is pulled in Ollama, otherwise hashing.

``sentence_transformers`` uses a multilingual model (e.g. intfloat/multilingual-e5-small)
for better semantic recall when the model can be downloaded.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from pathlib import Path

import httpx
import numpy as np

from app.core.config import settings

log = logging.getLogger(__name__)
_TOKEN = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)

STOPWORDS = set("""
a an the and or of to in on at for from with by is are was were be been am i me my we you your she her he it its
this that these those do does did have has had can could should would will shall may might what which who whom how
when where why since so very much more most some any all not no yes just also than then there here about into over
after before during while feel feeling felt get got please tell explain know want need help
pregnancy pregnant
hai hain ho hun hoon main mujhe mera meri mere aap aapka kya kab kaise kyun se ka ki ke ko me mein par aur bhi nahi
है हैं हो हूँ हूं मैं मुझे मेरा मेरी मेरे आप क्या कब कैसे क्यों से का की के को में पर और भी नहीं रहा रही रहे था थी
""".split())

SYNONYMS = {
    "chakkar": "dizziness", "चक्कर": "dizziness dizzy", "dizzy": "dizziness",
    "sujan": "swelling", "soojan": "swelling", "सूजन": "swelling", "swollen": "swelling",
    "anemia": "anaemia haemoglobin", "anaemia": "anemia haemoglobin", "hemoglobin": "haemoglobin",
    "khoon": "blood", "खून": "blood", "kami": "low", "कमी": "low",
    "ulti": "vomiting nausea", "उल्टी": "vomiting nausea", "thakan": "tiredness", "थकान": "tiredness tired",
    "bukhar": "fever", "बुखार": "fever", "sir": "head", "dard": "pain", "दर्द": "pain",
    "bachcha": "baby", "bacha": "baby", "बच्चा": "baby", "hil": "movement moving", "हलचल": "movement",
    "neend": "sleep", "नींद": "sleep", "khana": "food diet eat", "खाना": "food diet",
    "yojana": "scheme", "योजना": "scheme", "paisa": "money", "पैसे": "money",
    "tension": "stress worried", "chinta": "worried anxiety", "चिंता": "worried anxiety",
    "akela": "lonely", "akeli": "lonely", "अकेली": "lonely", "sad": "low mood sadness", "udaas": "sad low mood",
    "उदास": "sad low mood", "lonely": "alone loneliness",
    "sugar": "glucose diabetes", "शुगर": "glucose diabetes", "bp": "blood pressure",
    "iron": "ifa", "kab": "when", "goli": "tablet", "गोली": "tablet", "eat": "food diet", "foods": "food",
    # wider vocabulary for the larger knowledge base
    "hit": "violence hurt", "hits": "violence hurt", "beat": "violence hurt", "beats": "violence hurt",
    "abuse": "violence hurt", "marta": "violence hurt", "maarta": "violence hurt", "मारता": "violence hurt",
    "caesarean": "section operation", "cesarean": "section operation", "c-section": "section operation",
    "csection": "section operation", "operation": "section", "ऑपरेशन": "section operation", "सिज़ेरियन": "section operation",
    "loose": "diarrhoea", "motion": "diarrhoea stool", "motions": "diarrhoea stool", "dast": "diarrhoea",
    "दस्त": "diarrhoea", "diarrhea": "diarrhoea",
    "yellow": "jaundice", "peeliya": "jaundice", "पीलिया": "jaundice", "पीला": "jaundice yellow",
    "ogtt": "sugar glucose test", "solid": "complementary food", "weaning": "complementary food",
    "newborn": "baby infant", "infant": "baby newborn", "शिशु": "baby newborn", "नवजात": "newborn baby",
    "breastfeed": "breastfeeding milk", "breastfeeding": "breast milk", "doodh": "milk breastfeeding",
    "दूध": "milk breastfeeding", "stan": "breast", "स्तन": "breast",
    "safed": "white discharge", "सफ़ेद": "white discharge", "पानी": "water",
    "periods": "period menstruation", "mahwari": "period", "माहवारी": "period",
    "tika": "vaccine", "teeka": "vaccine", "टीका": "vaccine", "injection": "vaccine",
    "vrat": "fasting", "व्रत": "fasting", "roza": "fasting", "navratri": "fasting",
    "papita": "papaya", "पपीता": "papaya", "kabj": "constipation", "kabz": "constipation", "कब्ज़": "constipation",
    "jalan": "burning", "जलन": "burning heartburn", "acidity": "heartburn", "gas": "heartburn",
    "kamar": "back", "कमर": "back", "pet": "belly abdomen", "पेट": "belly abdomen",
    "pregnant": "pregnancy", "garbh": "pregnancy", "गर्भ": "pregnancy", "डिलीवरी": "delivery labour",
    "government": "scheme yojana", "sarkari": "scheme yojana", "सरकारी": "scheme yojana", "money": "cash scheme",
    "cash": "money scheme", "prasav": "delivery labour", "प्रसव": "delivery labour",
    "aanganwadi": "anganwadi", "आँगनवाड़ी": "anganwadi", "ration": "anganwadi nutrition",
    "store": "expressed keep", "storing": "expressed keep", "ayushman": "pmjay card", "card": "mcp", "certificate": "registration",
}


def _stem(w: str) -> str:
    for suf in ("iness", "ness", "ings", "ing", "ies", "ed", "es", "s", "y"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


class HashingEmbedder:
    dim = 2048

    def __init__(self) -> None:
        self.idf: np.ndarray | None = None

    def _features(self, text: str) -> list[tuple[str, float]]:
        raw = [w.lower() for w in _TOKEN.findall(text)]
        words = []
        for w in raw:
            if w in STOPWORDS or len(w) < 2:
                continue
            words.append(_stem(w))
            if w in SYNONYMS:
                words += [_stem(s) for s in SYNONYMS[w].split()]
        feats = [(w, 1.0) for w in words]
        feats += [(f"{a}_{b}", 0.7) for a, b in zip(words, words[1:])]
        for w in words:
            padded = f"#{w}#"
            feats += [(padded[i:i + 3], 0.25) for i in range(max(len(padded) - 2, 1))]
        return feats

    @staticmethod
    def _bucket(f: str) -> tuple[int, float]:
        h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
        return h % HashingEmbedder.dim, 1.0 if (h >> 63) & 1 else -1.0

    def fit(self, texts: list[str]) -> None:
        df = np.zeros(self.dim, dtype="float32")
        for t in texts:
            for b in {self._bucket(f)[0] for f, _ in self._features(t)}:
                df[b] += 1
        n = max(len(texts), 1)
        self.idf = np.log((1 + n) / (1 + df)).astype("float32") + 1.0

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for row, text in enumerate(texts):
            counts: dict[int, float] = {}
            signs: dict[int, float] = {}
            for f, w in self._features(text):
                b, sign = self._bucket(f)
                counts[b] = counts.get(b, 0.0) + w
                signs[b] = sign
            for b, c in counts.items():
                weight = (1 + math.log(c)) if c >= 1 else c
                if self.idf is not None:
                    weight *= float(self.idf[b])
                out[row, b] = signs[b] * weight
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


class SentenceTransformerEmbedder:  # pragma: no cover - optional heavy dependency
    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(settings.embedding_model)
        self.dim = self._model.get_sentence_embedding_dimension()

    def fit(self, texts: list[str]) -> None:
        pass

    def embed(self, texts: list[str]) -> np.ndarray:
        prefixed = [f"passage: {t}" for t in texts]
        return np.asarray(self._model.encode(prefixed, normalize_embeddings=True), dtype="float32")


class OllamaEmbedder:
    """Semantic vectors from an Ollama embedding model (nomic-embed-text by default)."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.embedding_ollama_model
        self.base = settings.llm_base_url.rstrip("/")

    def vectors(self, texts: list[str]) -> np.ndarray | None:
        if not texts:
            return np.zeros((0, 0), dtype="float32")
        try:
            with httpx.Client(timeout=max(settings.llm_timeout_seconds, 60)) as c:
                r = c.post(self.base + "/api/embed", json={"model": self.model, "input": texts})
                if r.status_code == 404:  # older Ollama: one text per call
                    rows = []
                    for t in texts:
                        rr = c.post(self.base + "/api/embeddings", json={"model": self.model, "prompt": t})
                        rr.raise_for_status()
                        rows.append(rr.json()["embedding"])
                else:
                    r.raise_for_status()
                    rows = r.json()["embeddings"]
            arr = np.asarray(rows, dtype="float32")
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            return arr / norms
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("Embedding model %s unavailable: %s", self.model, exc)
            return None


class HybridEmbedder:
    """Keyword vectors (HashingEmbedder: exact words, Hindi, Hinglish, synonyms) joined with semantic vectors
    from nomic-embed-text, so one inner product scores a weighted mix of both. Document vectors are cached on
    disk. If the embedding model stops answering, the semantic half is left empty and keyword search still works.
    """

    KEYWORD_WEIGHT, SEMANTIC_WEIGHT = 0.45, 0.55

    def __init__(self) -> None:
        self.keyword = HashingEmbedder()
        self.semantic = OllamaEmbedder()
        self.sem_dim = 0
        self.name = f"hybrid (keywords + {self.semantic.model})"

    @property
    def dim(self) -> int:
        return HashingEmbedder.dim + self.sem_dim

    def fit(self, texts: list[str]) -> None:
        self.keyword.fit(texts)

    def _join(self, kw: np.ndarray, sem: np.ndarray | None) -> np.ndarray:
        if not self.sem_dim:
            return kw
        if sem is None:
            sem = np.zeros((kw.shape[0], self.sem_dim), dtype="float32")
        return np.hstack([kw * math.sqrt(self.KEYWORD_WEIGHT), sem * math.sqrt(self.SEMANTIC_WEIGHT)]).astype("float32")

    def embed(self, texts: list[str]) -> np.ndarray:  # documents
        kw = self.keyword.embed(texts)
        key = hashlib.sha256((self.semantic.model + "\x00" + "\x00".join(texts)).encode()).hexdigest()[:20]
        cache = Path(settings.risk_model_path).parent / f"kb_vectors_{key}.npy"
        sem = None
        if cache.exists():
            try:
                sem = np.load(cache)
            except (OSError, ValueError):
                sem = None
        if sem is None:
            sem = self.semantic.vectors([f"search_document: {t}" for t in texts])
            if sem is not None:
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    np.save(cache, sem)
                except OSError:
                    pass
        if sem is None or sem.shape[0] != len(texts):
            self.sem_dim, self.name = 0, "keywords (embedding model unavailable)"
            return kw
        self.sem_dim = sem.shape[1]
        return self._join(kw, sem)

    def embed_query(self, texts: list[str]) -> np.ndarray:
        kw = self.keyword.embed(texts)
        if not self.sem_dim:
            return kw
        sem = self.semantic.vectors([f"search_query: {t}" for t in texts])
        if sem is not None and sem.shape[1] != self.sem_dim:
            sem = None
        return self._join(kw, sem)


def get_embedder():
    provider = settings.embedding_provider
    if provider == "sentence_transformers":
        return SentenceTransformerEmbedder()
    if provider == "hybrid":
        return HybridEmbedder()
    if provider == "auto":
        from app.ai.llm import has_model, ollama_models

        if has_model(settings.embedding_ollama_model, ollama_models()):
            return HybridEmbedder()
    return HashingEmbedder()
