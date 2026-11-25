"""Profiller: varsayılan, oluşturma/geçiş/silme, Türkçe ad, kasa ad alanı, bozuk dosyalar."""
from __future__ import annotations

import pytest

from kadraj.storage import profiles as P


def test_default_profile_is_created_and_active(clean_home):
    active = P.active_profile()
    assert active.id == P.DEFAULT_ID and active.active
    assert active.ui_mode == "simple"
    assert [p.id for p in P.list_profiles()] == [P.DEFAULT_ID]
    assert (clean_home / "profiles" / "default.json").exists()


def test_create_switch_and_vault_namespace(clean_home):
    p = P.create_profile("Ali Emre")
    assert p.id == "ali-emre"
    assert p.vault_namespace == "profile.ali-emre"
    switched = P.switch_profile(p.id)
    assert switched.active
    assert P.active_profile().id == "ali-emre"
    assert sum(1 for x in P.list_profiles() if x.active) == 1


def test_turkish_names_are_slugified():
    assert P.slugify("Çağrı Işık Öğrenci") == "cagri-isik-ogrenci"
    assert P.slugify("İzmir  İş") == "izmir-is"


def test_duplicate_invalid_and_reserved_names(clean_home):
    P.create_profile("Deneme")
    with pytest.raises(P.ProfileError) as dup:
        P.create_profile("deneme")
    assert dup.value.code == "exists"
    with pytest.raises(P.ProfileError) as bad:
        P.create_profile("!!!")
    assert bad.value.code == "name_invalid"
    with pytest.raises(P.ProfileError) as reserved:
        P.create_profile("default")
    assert reserved.value.code == "exists"
    with pytest.raises(P.ProfileError) as mode:
        P.create_profile("x", ui_mode="neon")
    assert mode.value.code == "mode_invalid"


def test_path_like_ids_are_rejected(clean_home):
    for bad in ["../default", "default/../x", "", "DEFAULT"]:
        with pytest.raises(P.ProfileError) as e:
            P.get_profile(bad)
        assert e.value.code == "not_found"


def test_update_keeps_other_fields(clean_home):
    p = P.create_profile("Kayit Modu", ui_mode="simple")
    P.update_profile(p.id, ui_mode="legend", a11y={"high_contrast": True})
    q = P.get_profile(p.id)
    assert q.ui_mode == "legend"
    assert q.a11y["high_contrast"] is True and q.a11y["large_text"] is False
    with pytest.raises(P.ProfileError):
        P.update_profile(p.id, ui_mode="neon")


def test_deleting_active_profile_falls_back_to_default(clean_home):
    p = P.create_profile("Geçici")
    P.switch_profile(p.id)
    P.delete_profile(p.id)
    assert P.active_profile().id == P.DEFAULT_ID
    assert not (clean_home / "profiles" / f"{p.id}.json").exists()
    assert [x.id for x in P.list_profiles()] == [P.DEFAULT_ID]


def test_default_cannot_be_deleted(clean_home):
    with pytest.raises(P.ProfileError) as e:
        P.delete_profile(P.DEFAULT_ID)
    assert e.value.code == "default_locked"


def test_corrupt_profile_file_is_hidden_and_active_falls_back(clean_home):
    p = P.create_profile("Bozuk")
    P.switch_profile(p.id)
    (clean_home / "profiles" / f"{p.id}.json").write_text("{nope", encoding="utf-8")
    assert p.id not in [x.id for x in P.list_profiles()]
    assert P.active_profile().id == P.DEFAULT_ID


def test_corrupt_default_file_uses_defaults(clean_home):
    P.active_profile()
    (clean_home / "profiles" / "default.json").write_text("zart", encoding="utf-8")
    d = P.active_profile()
    assert d.id == P.DEFAULT_ID and d.ui_mode == "simple"


def test_corrupt_index_resets_to_default(clean_home):
    p = P.create_profile("Bir")
    P.switch_profile(p.id)
    (clean_home / "profiles" / "index.json").write_text("zart", encoding="utf-8")
    assert P.active_profile().id == P.DEFAULT_ID
    assert [x.id for x in P.list_profiles()] == [P.DEFAULT_ID]


def test_error_message_is_plain_turkish(clean_home):
    P.create_profile("Tekrar")
    with pytest.raises(P.ProfileError) as e:
        P.create_profile("Tekrar")
    assert "profil" in str(e.value).lower()
