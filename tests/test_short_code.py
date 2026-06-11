"""Tests for app.services.short_code.generate_short_code_sync()"""

from app.services.short_code import generate_short_code_sync, _ensure_unique, _is_valid_code


class TestGenerateShortCodeSync:
    """Heuristic short code generation (no LLM)."""

    def test_database_lab(self):
        code = generate_short_code_sync("数据库实验4")
        assert code == "DB-LAB4"

    def test_data_mining_final(self):
        code = generate_short_code_sync("数据挖掘期末考试")
        assert code == "DM-FINAL"

    def test_javaweb_project(self):
        code = generate_short_code_sync("JavaWeb课程设计")
        assert code == "JAVAWEB-PROJECT"

    def test_english_parts_extracted(self):
        code = generate_short_code_sync("检查Javaweb系统")
        assert code.startswith("JAVAWEB")

    def test_empty_title_fallback(self):
        code = generate_short_code_sync("")
        assert code == "TASK"

    def test_pure_chinese_fallback(self):
        code = generate_short_code_sync("随便写点什么")
        # Fallback: first 4 chars of cleaned title (may include CJK)
        assert len(code) > 0

    def test_uniqueness_with_existing(self):
        existing = {"DB-LAB4"}
        code = generate_short_code_sync("数据库实验4", existing)
        assert code == "DB-LAB42"

    def test_no_uniqueness_conflict(self):
        existing = {"DB-LAB5"}
        code = generate_short_code_sync("数据库实验4", existing)
        assert code == "DB-LAB4"

    def test_suffix_only(self):
        # "实验" and "报告" are same length; "实验" comes first in _SUFFIX_MAP
        code = generate_short_code_sync("实验报告")
        assert code == "LAB"

    def test_number_extraction(self):
        code = generate_short_code_sync("数据库实验10")
        assert "10" in code


class TestHelpers:
    def test_is_valid_code(self):
        assert _is_valid_code("DB-LAB4")
        assert _is_valid_code("TASK")
        assert not _is_valid_code("ab")      # lowercase
        assert not _is_valid_code("A")       # too short
        assert not _is_valid_code("A B")     # space

    def test_ensure_unique_no_conflict(self):
        assert _ensure_unique("ABC", None) == "ABC"
        assert _ensure_unique("ABC", set()) == "ABC"
        assert _ensure_unique("ABC", {"XYZ"}) == "ABC"

    def test_ensure_unique_conflict(self):
        assert _ensure_unique("ABC", {"ABC"}) == "ABC2"
        assert _ensure_unique("ABC", {"ABC", "ABC2"}) == "ABC3"
