"""정규화·dedupe unit test (명세 §22, §29, §60)."""
from workers.discovery.normalize import content_hash, normalize_text, normalize_url, strip_markup
from workers.discovery.terms import STOPWORDS, dedupe_overlapping_terms, extract_ngrams


class TestNormalize:
    def test_strip_naver_bold_markup(self):
        # Naver 검색 API는 검색어를 <b>로 감싼다 — 검증된 스펙
        assert strip_markup("<b>오즈모포켓4</b> 축구촬영") == "오즈모포켓4 축구촬영"

    def test_markup_does_not_change_hash(self):
        a = content_hash("<b>로봇청소기</b> 물걸레 비교", "요약")
        b = content_hash("로봇청소기 물걸레 비교", "요약")
        assert a == b

    def test_url_normalization(self):
        assert (normalize_url("https://News.example.com/a/b/?utm=x#frag")
                == "https://news.example.com/a/b")

    def test_hash_stable_across_whitespace_and_punct(self):
        assert content_hash("청년  지원금, 상향!", None) == content_hash("청년 지원금 상향", None)

    def test_different_content_different_hash(self):
        assert content_hash("제목 A", None) != content_hash("제목 B", None)


class TestTerms:
    def test_stopword_single_gram_excluded(self):
        grams = extract_ngrams("로봇청소기 추천")
        assert "추천" not in grams
        assert "로봇청소기" in grams
        assert "로봇청소기 추천" in grams  # stopword 포함 2-gram은 허용

    def test_ngram_range_1_to_4(self):
        grams = extract_ngrams("하나 둘셋 넷다섯 여섯일곱 여덟아홉")
        assert max(len(g.split()) for g in grams) == 4

    def test_overlap_dedupe_prefers_longer_when_same_docs(self):
        docs = {"오즈모포켓4": {"d1", "d2", "d3"},
                "오즈모포켓4 축구촬영": {"d1", "d2", "d3"}}
        accepted = dedupe_overlapping_terms(docs)
        assert accepted == ["오즈모포켓4 축구촬영"]

    def test_overlap_dedupe_keeps_broader_term_with_more_docs(self):
        docs = {"오즈모포켓4": {"d1", "d2", "d3", "d4", "d5"},
                "오즈모포켓4 축구촬영": {"d1", "d2"}}
        accepted = dedupe_overlapping_terms(docs)
        assert "오즈모포켓4" in accepted
        assert "오즈모포켓4 축구촬영" not in accepted

    def test_unrelated_terms_both_kept(self):
        docs = {"로봇청소기 물걸레": {"a", "b", "c"}, "청년 지원금": {"x", "y", "z"}}
        assert set(dedupe_overlapping_terms(docs)) == set(docs)

    def test_stopwords_are_normalized_form(self):
        assert all(s == s.lower().strip() for s in STOPWORDS)
