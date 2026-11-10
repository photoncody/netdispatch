"""Raw (hand-edited) unattend templates: storage, validation, rendering."""

from __future__ import annotations

import pytest

from app.unattend import computer_name, render

TPL = "<unattend><ComputerName>{{ hostname }}</ComputerName></unattend>"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("LAB-PC-07", "LAB-PC-07"),
        ("Lab PC 7", "Lab-PC-7"),
        ("  evil ${x} && reboot ", "evil-x-reboot"),
        ("Bürorechner", "B-rorechner"),
        ("a-very-long-computer-name", "a-very-long-com"),
        ("abcdefghijklmn-x", "abcdefghijklmn"),  # no trailing hyphen after the cut
        ("12345", "ND-ABCD01"),  # all digits is not a valid computer name
        ("!!!", "ND-ABCD01"),
        (None, "ND-ABCD01"),
        ("", "ND-ABCD01"),
    ],
)
def test_computer_name_is_valid_for_windows(label, expected):
    assert computer_name(label, "52:54:00:ab:cd:01") == expected


def test_crud_and_validation(admin, settings):
    r = admin.post("/api/unattend", json={"name": "Basic", "description": "d", "content": TPL})
    assert r.status_code == 201, r.text
    tpl = r.json()
    assert tpl["generated"] is False
    assert (settings.configs_dir / tpl["filename"]).read_text() == TPL

    assert admin.post("/api/unattend", json={"name": "Basic", "content": TPL}).status_code == 409
    bad = admin.post("/api/unattend", json={"name": "Bad", "content": "<unattend>"})
    assert bad.status_code == 422 and "well-formed" in bad.json()["detail"]
    bad = admin.post("/api/unattend", json={"name": "Bad", "content": "<a>{% if %}</a>"})
    assert bad.status_code == 422 and "template error" in bad.json()["detail"]

    r = admin.put(f"/api/unattend/{tpl['id']}", json={"name": "Basic 2", "content": TPL.replace("unattend>", "u>")})
    assert r.status_code == 200, r.text
    assert admin.get(f"/api/unattend/{tpl['id']}").json()["content"].startswith("<u>")

    preview = admin.post(f"/api/unattend/{tpl['id']}/preview").json()
    assert "<ComputerName>ND-123456</ComputerName>" in preview["rendered"]

    assert admin.delete(f"/api/unattend/{tpl['id']}").status_code == 204
    assert admin.get(f"/api/unattend/{tpl['id']}").status_code == 404
    assert not (settings.configs_dir / tpl["filename"]).exists()


def test_crlf_template_renders_with_crlf(admin, settings):
    content = "<unattend>\r\n<ComputerName>{{ hostname }}</ComputerName>\r\n</unattend>\r\n"
    tpl = admin.post("/api/unattend", json={"name": "CRLF", "content": content}).json()
    assert (settings.configs_dir / tpl["filename"]).read_bytes() == content.encode()
    assert admin.get(f"/api/unattend/{tpl['id']}").json()["content"] == content

    rendered = admin.post(f"/api/unattend/{tpl['id']}/preview").json()["rendered"]
    assert rendered == "<unattend>\r\n<ComputerName>ND-123456</ComputerName>\r\n</unattend>\r\n"

    # The browser editor sends LF; a file that had CRLF keeps it.
    admin.put(f"/api/unattend/{tpl['id']}", json={"name": "CRLF", "content": content.replace("\r\n", "\n")})
    assert (settings.configs_dir / tpl["filename"]).read_bytes() == content.encode()


def test_lf_template_stays_lf():
    assert render("<a>\n{{ hostname }}\n</a>\n", {"hostname": "x"}) == "<a>\nx\n</a>\n"


def test_dashboard_includes_generator(admin):
    page = admin.get("/").text
    assert 'id="gen-region"' in page and 'id="gen-switches"' in page
    assert "/static/unattend-generator.js?v=" in page
    assert "...unattendGenerator()," in page


def test_template_files_dropped_in_are_indexed(admin, settings):
    (settings.configs_dir / "dropped.xml").write_text(TPL)
    names = [t["name"] for t in admin.get("/api/unattend").json()]
    assert "dropped" in names
