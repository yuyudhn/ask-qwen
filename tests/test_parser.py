from ask_qwen.parser import extract_answer

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

    def test_all_mode_labels_stripped(self):
        # label mode (Auto/Thinking/Fast) di area input ikut ter-capture di <main>
        for mode in ("Auto", "Thinking", "Fast"):
            assert extract_answer(f"jawaban\n{mode}") == "jawaban"

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

