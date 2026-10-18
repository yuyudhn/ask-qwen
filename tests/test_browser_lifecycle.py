import threading
import time

import pytest

from ask_qwen import browser, config


def test_playwright_cookies_normalize_exported_same_site_values():
    cookies = browser._playwright_cookies([
        {"name": "a", "value": "1", "sameSite": "lax", "partitionKey": {}},
        {"name": "b", "value": "2", "sameSite": "strict"},
        {"name": "c", "value": "3", "sameSite": "unknown"},
        {"name": "d", "value": "4", "sameSite": None},
    ])

    assert [cookie.get("sameSite") for cookie in cookies] == ["Lax", "Strict", "None", None]
    assert "partitionKey" not in cookies[0]
    assert "sameSite" not in cookies[3]


class _SequenceLocator:
    def __init__(self, values):
        self.values = iter(values)

    def inner_text(self):
        return next(self.values)


class _ResponsePage:
    def __init__(self, main_values, send_visible=True):
        self.main = _SequenceLocator(main_values)
        self.send_visible = send_visible

    def locator(self, selector):
        if selector == config.MAIN_SEL:
            return self.main
        return _VisibleLocator(self.send_visible)


class _VisibleLocator:
    def __init__(self, visible):
        self.visible = visible

    def is_visible(self):
        return self.visible


class _HistoryPage:
    url = "https://chat.qwen.ai/"

    def __init__(self, items):
        self.items = items
        self.generating = False
        for item in items:
            item.page = self
        self.navigated = []

    def locator(self, selector):
        if selector == config.CHAT_HISTORY_SEL:
            return _HistoryLocator(self.items)
        if selector == config.STOP_BTN_SEL:
            return _VisibleLocator(self.generating)
        raise AssertionError(f"unexpected selector: {selector}")

    def wait_for_function(self, *args, **kwargs):
        # klik pada _HistoryItem langsung mengubah url → kondisi terpenuhi
        return None

    def wait_for_selector(self, *args, **kwargs):
        return None

    def goto(self, url, **kwargs):
        self.navigated.append(url)
        self.url = url


class _SearchPage:
    url = "https://chat.qwen.ai/"

    def __init__(self):
        self.value = ""
        self.generating = False
        self.items = []
        self.history_item = _HistoryItem("CAN Bus", "https://chat.qwen.ai/c/abc")
        self.history_item.page = self

    def locator(self, selector):
        if selector == config.SEARCH_INPUT_SEL:
            return _SearchInput(self)
        if selector == config.CHAT_HISTORY_SEL:
            return _HistoryLocator([self.history_item])
        if selector == config.STOP_BTN_SEL:
            return _VisibleLocator(self.generating)
        raise AssertionError(f"unexpected selector: {selector}")

    def wait_for_selector(self, *args, **kwargs):
        return None

    def wait_for_function(self, *args, **kwargs):
        return None

    def goto(self, url, **kwargs):
        self.url = url


class _SearchInput:
    def __init__(self, page):
        self.page = page

    def fill(self, value):
        self.page.value = value


class _StopPage:
    def __init__(self, stop_visible):
        self.stop_visible = stop_visible

    def locator(self, selector):
        if selector == config.STOP_BTN_SEL:
            return _StopLocator(self)
        raise AssertionError(f"unexpected selector: {selector}")


class _StopLocator:
    def __init__(self, page):
        self.page = page

    def is_visible(self):
        return self.page.stop_visible

    def click(self):
        self.page.clicked = True


def test_stop_generation_clicks_stop_when_active():
    page = _StopPage(True)
    page.clicked = False
    backend = browser.PlaywrightBackend()
    backend._page = page
    backend._ctx = object()
    backend._is_logged_in = lambda: True

    # Deliberately fails until the stop action exists.
    assert backend.stop_generation() == "Generasi dihentikan."
    assert page.clicked


def test_stop_generation_reports_idle_state():
    page = _StopPage(False)
    backend = browser.PlaywrightBackend()
    backend._page = page
    backend._ctx = object()
    backend._is_logged_in = lambda: True

    assert backend.stop_generation() == "Tidak ada generasi aktif."


def test_get_status_reports_browser_state(monkeypatch):
    monkeypatch.setattr(browser, "_get_thinking_mode", lambda: None)
    page = _StopPage(False)
    page.url = "https://chat.qwen.ai/c/abc"
    backend = browser.PlaywrightBackend()
    backend._page = page
    backend._ctx = object()
    backend.has_active_chat = True
    backend._is_logged_in = lambda: True

    status = backend.get_status()

    assert status == {
        "logged_in": True,
        "generating": False,
        "active_chat": True,
        "thinking_mode": None,
        "url": "https://chat.qwen.ai/c/abc",
    }


def test_search_chats_fills_input_and_returns_matching_items():
    page = _SearchPage()
    backend = browser.PlaywrightBackend()
    backend._page = page
    backend._ctx = object()
    backend._is_logged_in = lambda: True

    result = backend.search_chats("CAN Bus")

    assert page.value == ""
    assert result == [{"title": "CAN Bus", "url": "https://chat.qwen.ai/c/abc", "id": "abc"}]


class _HistoryLocator:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class _HistoryItem:
    def __init__(self, title, url):
        self.title = title
        self.url = url
        self.page = None

    def inner_text(self):
        return self.title

    def click(self, timeout=None):
        self.page.url = self.url


def test_profile_lock_is_reported_before_launch(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "SingletonLock").touch()
    monkeypatch.setattr(config, "PROFILE_DIR", profile)

    backend = browser.PlaywrightBackend()

    with pytest.raises(browser.ProfileLocked, match="profile Qwen sedang dipakai"):
        backend._launch()


def test_browser_operations_are_serialized():
    backend = browser.PlaywrightBackend()
    running = 0
    max_running = 0
    first_entered = threading.Event()
    release = threading.Event()

    def operation():
        nonlocal running, max_running
        with backend._operation_lock:
            running += 1
            max_running = max(max_running, running)
            first_entered.set()
            release.wait(timeout=1)
            running -= 1

    first = threading.Thread(target=operation)
    second = threading.Thread(target=operation)
    first.start()
    assert first_entered.wait(timeout=1)
    second.start()
    assert second.is_alive()
    release.set()
    first.join()
    second.join()

    assert max_running == 1


def test_launch_stops_playwright_when_context_start_fails(tmp_path, monkeypatch):
    class FakePlaywright:
        def __init__(self):
            self.stopped = False
            self.chromium = self

        def launch_persistent_context(self, *_args, **_kwargs):
            raise RuntimeError("launch failed")

        def stop(self):
            self.stopped = True

    fake = FakePlaywright()
    monkeypatch.setattr(browser, "sync_playwright", lambda: _Started(fake))
    monkeypatch.setattr(config, "PROFILE_DIR", tmp_path / "profile")

    backend = browser.PlaywrightBackend()
    with pytest.raises(RuntimeError, match="launch failed"):
        backend._launch()

    assert fake.stopped
    assert backend._pw is None
    assert backend._ctx is None


def test_wait_response_done_ignores_pre_send_transcript(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(config, "RESPONSE_STABLE_MS", 0)
    page = _GenPage(
        main=["old transcript", "new answer", "new answer"],
        stop=[False, True, False, False],
    )

    answer = browser.PlaywrightBackend()._wait_response_done(
        page, previous_text="old transcript"
    )

    assert answer == "new answer"


def test_list_chats_reads_history_links():
    page = _HistoryPage(
        [
            _HistoryItem("Ide project", "https://chat.qwen.ai/c/abc"),
            _HistoryItem("Chat kedua", "https://chat.qwen.ai/c/def"),
        ]
    )
    backend = browser.PlaywrightBackend()
    backend._page = page
    backend._ctx = object()
    backend._is_logged_in = lambda: True

    assert backend.list_chats() == [
        {"title": "Ide project", "url": "https://chat.qwen.ai/c/abc", "id": "abc"},
        {"title": "Chat kedua", "url": "https://chat.qwen.ai/c/def", "id": "def"},
    ]
    assert page.url == "https://chat.qwen.ai/"  # kembali ke halaman semula


def test_list_chats_refuses_while_generating():
    page = _HistoryPage([_HistoryItem("x", "https://chat.qwen.ai/c/a")])
    page.generating = True
    backend = browser.PlaywrightBackend()
    backend._page = page
    backend._ctx = object()
    backend._is_logged_in = lambda: True

    with pytest.raises(RuntimeError, match="Generasi sedang berjalan"):
        backend.list_chats()


def test_read_chat_rejects_non_qwen_url():
    with pytest.raises(ValueError, match="chat.qwen.ai"):
        browser.PlaywrightBackend().read_chat("https://example.com/c/abc")


class _Started:
    def __init__(self, value):
        self.value = value

    def start(self):
        return self.value


class _RepeatingLocator:
    """Locator yang mengulang nilai terakhir setelah urutan habis."""

    def __init__(self, values):
        self.values = list(values)
        self._last = self.values.pop(0) if self.values else None

    def inner_text(self):
        if self.values:
            self._last = self.values.pop(0)
        return self._last


class _BoolLocator:
    def __init__(self, states):
        self.states = list(states)
        self._last = self.states.pop(0) if self.states else False

    def is_visible(self):
        if self.states:
            self._last = self.states.pop(0)
        return self._last


class _GenPage:
    """Halaman dengan sinyal tombol Stop terverifikasi dari DOM Qwen:
    Stop muncul saat generasi, hilang saat selesai."""

    def __init__(self, main, stop):
        self.main = _RepeatingLocator(main)
        self.stop = _BoolLocator(stop)

    def locator(self, selector):
        if selector == config.MAIN_SEL:
            return self.main
        if selector == config.STOP_BTN_SEL:
            return self.stop
        raise AssertionError(f"unexpected selector: {selector}")


def test_wait_response_done_uses_stop_button_signal(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    page = _GenPage(
        main=["old", "user + partial", "user + answer", "user + answer"],
        stop=[False, True, True, False],
    )

    answer = browser.PlaywrightBackend()._wait_response_done(
        page, previous_text="old"
    )

    assert answer == "user + answer"


def test_wait_response_done_falls_back_to_text_stability(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(config, "RESPONSE_STABLE_MS", 0)
    monkeypatch.setattr(config, "RESPONSE_FALLBACK_GRACE_S", 0.0)
    # Stop tidak pernah terlihat (selector berubah) → fallback teks stabil
    page = _GenPage(main=["old", "user + answer"], stop=[False])

    answer = browser.PlaywrightBackend()._wait_response_done(
        page, previous_text="old"
    )

    assert answer == "user + answer"


def test_wait_response_done_raises_when_response_never_starts(monkeypatch):
    from playwright.sync_api import TimeoutError as PWTimeoutError

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(config, "RESPONSE_START_TIMEOUT_MS", 0)
    page = _GenPage(main=["old"], stop=[False])

    with pytest.raises(PWTimeoutError):
        browser.PlaywrightBackend()._wait_response_done(
            page, previous_text="old"
        )


class _StoragePage:
    """Fake page untuk apply_thinking_mode: localStorage + goto recorder."""

    def __init__(self, url="https://chat.qwen.ai/c/abc"):
        self.url = url
        self.storage = {}
        self.gotos = []

    def evaluate(self, fn, arg):
        assert "qwen-thinking_mode" in fn
        self.storage["qwen-thinking_mode"] = arg

    def goto(self, url, **kwargs):
        self.gotos.append(url)
        self.url = url

    def wait_for_selector(self, *args, **kwargs):
        return None

    def wait_for_timeout(self, ms):
        return None


def _mode_backend(page):
    backend = browser.PlaywrightBackend()
    backend._page = page
    backend._ctx = object()
    backend._is_logged_in = lambda: True
    return backend


def test_apply_thinking_mode_sets_localstorage():
    page = _StoragePage()
    backend = _mode_backend(page)

    result = backend.apply_thinking_mode("Thinking")

    assert page.storage["qwen-thinking_mode"] == "Thinking"
    assert backend.applied_mode == "Thinking"
    assert page.gotos == ["https://chat.qwen.ai/c/abc"]  # reload URL aktif
    assert "Thinking" in result


def test_apply_thinking_mode_idempotent():
    page = _StoragePage()
    backend = _mode_backend(page)
    backend.apply_thinking_mode("Fast")

    result = backend.apply_thinking_mode("Fast")

    assert page.gotos == ["https://chat.qwen.ai/c/abc"]  # reload hanya sekali
    assert "sudah" in result


class _ChatPage:
    url = "https://chat.qwen.ai/"

    def __init__(self, messages):
        self.messages = messages

    def goto(self, url, **kwargs):
        self.url = url

    def wait_for_selector(self, *args, **kwargs):
        return None


def test_open_chat_navigates_and_marks_active():
    page = _ChatPage([{"role": "user", "content": "halo"}])
    backend = browser.PlaywrightBackend()
    backend._page = page
    backend._ctx = object()
    backend._is_logged_in = lambda: True
    backend._extract_messages = lambda _page: _page.messages

    messages = backend.open_chat("https://chat.qwen.ai/c/abc")

    assert messages == [{"role": "user", "content": "halo"}]
    assert backend.has_active_chat
    assert page.url == "https://chat.qwen.ai/c/abc"


def test_open_chat_rejects_non_qwen_url():
    with pytest.raises(ValueError, match="chat.qwen.ai"):
        browser.PlaywrightBackend().open_chat("https://example.com/c/abc")


def test_normalize_chat_ref_accepts_bare_chat_id():
    assert browser.normalize_chat_ref("abc123XYZ-_") == "https://chat.qwen.ai/c/abc123XYZ-_"


def test_normalize_chat_ref_passes_through_full_url():
    url = "https://chat.qwen.ai/c/abc"
    assert browser.normalize_chat_ref(url) == url


def test_normalize_chat_ref_rejects_garbage():
    with pytest.raises(ValueError, match="Referensi chat"):
        browser.normalize_chat_ref("not a chat ref")


def test_wait_response_done_streams_clean_line_deltas(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    page = _GenPage(
        main=[
            "old",
            "oldUser\nThinking completed\npartial",
            "oldUser\nThinking completed\nfull answer\nAuto\nI prefer this response",
            "oldUser\nThinking completed\nfull answer\nAuto\nI prefer this response",
        ],
        stop=[False, True, True, False],
    )
    deltas = []

    browser.PlaywrightBackend()._wait_response_done(
        page, previous_text="old", on_delta=deltas.append
    )

    # noise UI (marker thinking, "Auto") disaring; baris dikirim utuh per \n;
    # token "partial" tertulis ulang DOM (re-render) sebelum terkirim → hilang,
    # itu trade-off streaming best-effort
    assert deltas == ["User\n", "full answer\n"]
