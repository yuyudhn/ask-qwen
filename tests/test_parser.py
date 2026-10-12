from ask_qwen.parser import clean_message, extract_answer, parse_messages

# Fixture: pola hasil verifikasi live 2026-09-08.
# inner_text() <main> saat dropdown reasoning COLLAPSED: isi reasoning
# TIDAK ikut terbaca — hanya label "Thinking completed".
DOM_TEXT = """Balas hanya dengan kata: OK

Thinking completed
OK

Auto
AI-generated content may not be accurate."""


class TestExtractAnswer:
    def test_simple_strips_everything_but_answer(self):
        assert extract_answer(DOM_TEXT, user_message="Balas hanya dengan kata: OK") == "OK"

    def test_no_user_message_given(self):
        result = extract_answer(DOM_TEXT)
        assert "OK" in result
        assert "Thinking completed" not in result
        assert "Balas hanya" not in result

    def test_disclaimer_always_stripped(self):
        result = extract_answer("jawaban\nAI-generated content may not be accurate.")
        assert "AI-generated" not in result
        assert result == "jawaban"

    def test_auto_line_stripped(self):
        result = extract_answer("jawaban\nAuto")
        assert result == "jawaban"

    def test_multiline_answer_preserved(self):
        text = "tanya\n\nThinking completed\n\nbaris 1\nbaris 2"
        assert extract_answer(text, user_message="tanya") == "baris 1\nbaris 2"

    def test_answer_with_marker_inside_keeps_last_block(self):
        # jawaban yang mengutip "Thinking completed" — ambil setelah marker terakhir
        text = "tanya\n\nThinking completed\nThinking completed itu bagian jawaban"
        assert extract_answer(text, user_message="tanya") == "Thinking completed itu bagian jawaban"

    def test_empty(self):
        assert extract_answer("") == ""

    def test_no_marker_no_user(self):
        assert extract_answer("plain answer") == "plain answer"


class TestParseMessages:
    def test_user_and_assistant_pair(self):
        pairs = [
            ("user", "pertanyaan user"),
            ("assistant", "Thinking completed\njawaban asisten"),
        ]
        assert parse_messages(pairs) == [
            {"role": "user", "content": "pertanyaan user"},
            {"role": "assistant", "content": "jawaban asisten"},
        ]

    def test_multi_turn_order_preserved(self):
        pairs = [
            ("user", "satu"),
            ("assistant", "Thinking completed\njawaban satu"),
            ("user", "dua"),
            ("assistant", "Thinking completed\njawaban dua"),
        ]
        result = parse_messages(pairs)
        assert [m["role"] for m in result] == ["user", "assistant", "user", "assistant"]
        assert result[3]["content"] == "jawaban dua"

    def test_assistant_disclaimer_stripped_per_message(self):
        result = parse_messages(
            [("assistant", "Thinking completed\njawaban\nAI-generated content may not be accurate.")]
        )
        assert result == [{"role": "assistant", "content": "jawaban"}]

    def test_empty_messages_dropped(self):
        assert parse_messages([("user", "  \n "), ("assistant", "")]) == []

    def test_clean_user_keeps_text_as_is(self):
        # _strip_noise_lines memang membuang baris kosong — itu perilaku eksisting
        assert clean_message("user", "halo\n\ndunia") == "halo\ndunia"

    def test_clean_assistant_without_marker(self):
        assert clean_message("assistant", "jawaban langsung") == "jawaban langsung"


# Fixture: Qwen A/B comparison UI (live 2026-10-12)
COMPARISON_TEXT = """Say OK only

This feedback will help us evaluate and improve Qwen's performance.

Which response do you prefer? Select one to continue.

Response 1
Thinking completed
OK
I prefer this response
Response 2
Thinking completed
OK
I prefer this response
Auto
AI-generated content may not be accurate."""


class TestComparisonUI:
    def test_extract_answer_from_comparison(self):
        result = extract_answer(COMPARISON_TEXT, user_message="Say OK only")
        assert result == "OK"

    def test_comparison_noise_fully_stripped(self):
        result = extract_answer(COMPARISON_TEXT, user_message="Say OK only")
        assert "prefer" not in result
        assert "Response" not in result
        assert "feedback" not in result

    def test_normal_text_unaffected_by_comparison_strip(self):
        assert extract_answer(DOM_TEXT, user_message="Balas hanya dengan kata: OK") == "OK"

    def test_clean_message_strips_comparison(self):
        raw = """This feedback will help us evaluate and improve Qwen's performance.
Which response do you prefer? Select one to continue.
Response 1
Thinking completed
hello world
I prefer this response
Response 2
Thinking completed
hello world
I prefer this response"""
        assert clean_message("assistant", raw) == "hello world"
