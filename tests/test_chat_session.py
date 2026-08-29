from __future__ import annotations

from eve.ai.base import ToolCall, assistant, tool_result, user
from eve.chat.session import ChatSession, SessionStore


def test_session_gets_an_id_and_title() -> None:
    session = ChatSession()
    assert len(session.id) == 12
    session.add(user("Uma pergunta bem comprida sobre alguma coisa em particular"))
    assert session.title.startswith("Uma pergunta")
    assert len(session.title) <= 60


def test_title_comes_from_the_first_user_message() -> None:
    session = ChatSession()
    session.add(assistant("olá"))
    assert session.title == ""
    session.add(user("primeira"))
    session.add(user("segunda"))
    assert session.title == "primeira"


def test_history_is_limited() -> None:
    session = ChatSession()
    for i in range(60):
        session.add(user(f"m{i}"))
    assert len(session.history(limit=10)) == 10
    assert session.history(limit=10)[-1].content == "m59"


def test_history_never_starts_with_an_orphan_tool_message() -> None:
    """Uma resposta de ferramenta sem a chamada que a gerou confunde o modelo."""
    session = ChatSession()
    call = ToolCall("app.open", {"name": "Safari"})
    session.add(user("abra o safari"))
    session.add(assistant("", [call]))
    session.add(tool_result(call, "ok"))
    session.add(assistant("abri"))

    recorte = session.history(limit=2)
    assert recorte[0].role != "tool"


def test_store_creates_and_reuses() -> None:
    store = SessionStore()
    primeira = store.get_or_create()
    assert store.get_or_create(primeira.id) is primeira
    assert len(store) == 1


def test_store_creates_with_a_given_id() -> None:
    store = SessionStore()
    assert store.get_or_create("minha-sessao").id == "minha-sessao"


def test_store_evicts_the_oldest() -> None:
    store = SessionStore(max_sessions=3)
    ids = [store.get_or_create().id for _ in range(5)]
    assert len(store) == 3
    assert store.get(ids[0]) is None
    assert store.get(ids[-1]) is not None


def test_using_a_session_keeps_it_alive() -> None:
    store = SessionStore(max_sessions=2)
    primeira = store.get_or_create()
    store.get_or_create()
    store.get_or_create(primeira.id)  # toca na primeira
    store.get_or_create()  # despeja a segunda, não a primeira
    assert store.get(primeira.id) is not None


def test_delete() -> None:
    store = SessionStore()
    session = store.get_or_create()
    assert store.delete(session.id) is True
    assert store.delete(session.id) is False


def test_all_is_sorted_by_recency() -> None:
    store = SessionStore()
    a = store.get_or_create()
    b = store.get_or_create()
    a.add(user("mais nova"))
    assert [s.id for s in store.all()] == [a.id, b.id]


def test_describe() -> None:
    session = ChatSession()
    session.add(user("oi"))
    data = session.describe()
    assert data["messages"] == 1
    assert data["title"] == "oi"
    assert data["updated_at"] >= data["created_at"]
