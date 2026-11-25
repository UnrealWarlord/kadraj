"""Düğüm editörü çekirdeği: grafik modeli, topolojik yürütme, önbellek/yalnız-bu-düğüm, seed kilidi, ham çekmece, matris, geçmiş."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from kadraj.ai_engine.nodes.executor import Executor, Run, validate_raw
from kadraj.ai_engine.nodes.graph import CycleError, Graph, SPECS, compatible, default_flow
from kadraj.ai_engine.pipeline.gateway import AIGateway
from kadraj.ai_engine.pipeline.machine import Character, Location, SeriesProject
from kadraj.ai_engine.providers.base import default_registry
from kadraj.ai_engine.providers.install import install_all

from .conftest import FakeDB, FakeSettings, FakeVault

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg yok")


# ============================================================================== grafik
def test_graph_types_cycles_order_and_validation():
    g = default_flow()
    assert g.validate() == [] and len(g.nodes) == 8 and len(g.connections) == 7
    order = g.order()
    assert order.index("logline_01") < order.index("llm_01") < order.index("gorsel_01") < order.index("upscale_01") < order.index("video_01") < order.index("cikis_01")
    assert g.upstream("video_01") == [i for i in order if i in {"logline_01", "llm_01", "gorsel_01", "upscale_01"}]
    with pytest.raises(ValueError):
        g.connect("logline_01", "text", "upscale_01", "image")                      # text → image uyumsuz
    with pytest.raises(CycleError):
        g.connect("upscale_01", "image", "gorsel_01", "reference")                   # gorsel → upscale → gorsel döngüsü
    with pytest.raises(CycleError):
        g.connect("gorsel_01", "image", "gorsel_01", "reference")                    # kendine bağlanamaz
    g.remove("llm_01")
    assert "llm_01" not in g.nodes and "prompt" not in g.nodes["gorsel_01"].inputs and "missing:gorsel_01.prompt" in g.validate()
    g2 = Graph()
    g2.add("logline", id="a")
    g2.add("llm", id="b")
    assert g2.validate() == ["no_output"] and {s for s in SPECS} == {"logline", "llm", "reference", "image", "upscale", "video", "audio", "output"}


def test_graph_serialization_roundtrip_and_broken_links_are_dropped():
    g = default_flow()
    g.nodes["gorsel_01"].params["seed"] = 7
    g.nodes["gorsel_01"].raw = {"prompt": "ham", "negative": "bulanık", "json": '{"cfg": 5}'}
    d = json.loads(json.dumps(g.to_dict()))
    g2 = Graph.from_dict(d)
    assert g2.to_dict() == g.to_dict() and g2.nodes["gorsel_01"].raw["negative"] == "bulanık"
    d["nodes"].append({"id": "x", "type": "bogus"})
    d["nodes"][3]["inputs"]["prompt"] = ["yok_dugum", "text"]
    g3 = Graph.from_dict(d)
    assert "x" not in g3.nodes and "prompt" not in g3.nodes["gorsel_01"].inputs


# ============================================================================== yürütücü
@pytest.fixture()
def ex(tmp_path, world):
    settings = FakeSettings(**{"providers.video.base_url": "https://vid.example", "providers.video.status_path": "/v/{id}"})
    reg = default_registry(settings)
    install_all(reg, settings=settings, transport=world.rec.transport, sleep=lambda s: None)
    db = FakeDB()
    gw = AIGateway(reg, FakeVault(anthropic="sk", bfl_flux="b", video="v", elevenlabs="e"), settings, db=lambda: db)
    gw.local_up.update({"comfyui": False, "ollama": False})
    world.llm_fn = lambda body: "An old ferry in morning fog, warm light."
    e = Executor(gw, tmp_path / "work")
    e.db = db
    return e


@needs_ffmpeg
def test_full_run_executes_in_order_and_tracks_cost_and_history(ex, world):
    g = default_flow()
    run = ex.run(g)
    assert run.status == "done" and [r.status for r in run.rows()] == ["done"] * 8 and run.mode == "all"
    img = Image.open(run.results["gorsel_01"].outputs["image"])
    up = Image.open(run.results["upscale_01"].outputs["image"])
    assert up.size == (img.width * 2, img.height * 2) and run.results["upscale_01"].provider == "local"          # ComfyUI yok → yerel Lanczos
    assert run.results["gorsel_01"].provider == "bfl_flux" and run.results["gorsel_01"].cost == pytest.approx(0.04)
    assert run.results["llm_01"].model == "claude-haiku-5-5" and run.results["llm_01"].tokens > 0
    assert Path(run.results["video_01"].outputs["video"]).read_bytes()[4:8] == b"ftyp" and Path(run.results["video_01"].outputs["last_frame"]).exists()
    assert Path(run.results["ses_01"].outputs["audio"]).suffix == ".wav" and Path(run.results["cikis_01"].outputs["files"][0]).exists()
    assert run.cost > 0.14 and run.cost == pytest.approx(sum(u["cost_usd"] for u in ex.db.usage), abs=1e-6)
    prompts = [json.loads(r.content) for r in world.requests if r.url.host == "api.bfl.ai" and r.method == "POST"]
    assert prompts[0]["prompt"] == "An old ferry in morning fog, warm light."                                       # LLM çıktısı görsele aktı
    assert ex.history[0] is run and Run.from_dict(json.loads(json.dumps(run.to_dict()))).to_dict() == run.to_dict()


@needs_ffmpeg
def test_seed_lock_rnd_and_single_node_run_uses_cache(ex, world):
    g = default_flow()
    n = g.nodes["gorsel_01"]
    ex.run(g)
    seeds = [json.loads(r.content)["seed"] for r in world.requests if r.url.host == "api.bfl.ai" and r.method == "POST"]
    ex.run(g, only="gorsel_01")
    seeds += [json.loads(r.content)["seed"] for r in world.requests if r.url.host == "api.bfl.ai" and r.method == "POST"][len(seeds):]
    assert len(seeds) == 2 and seeds[0] != seeds[1]                                                             # kilitsiz: her seferinde yeni tohum
    n.params.update(seed=4242, seed_locked=True)
    before = len([r for r in world.requests if r.url.host == "api.anthropic.com"])
    run = ex.run(g, only="gorsel_01")
    assert run.mode == "node" and list(run.results) == ["gorsel_01"] and run.results["gorsel_01"].params["seed"] == 4242
    assert len([r for r in world.requests if r.url.host == "api.anthropic.com"]) == before                       # LLM yeniden çağrılmadı (önbellek)
    assert json.loads([r for r in world.requests if r.url.host == "api.bfl.ai" and r.method == "POST"][-1].content)["seed"] == 4242
    rnd = n.randomize_seed()
    assert n.params["seed"] == rnd and n.params["seed_locked"] is True and rnd != 4242
    fresh = Executor(ex.gw, ex.workdir)
    run = fresh.run(g, only="gorsel_01")                                                                        # önbellek boş → girdi eksik
    assert run.status == "error" and run.results["gorsel_01"].error_code == "ai.missing_input"


@needs_ffmpeg
def test_raw_drawer_overrides_prompt_negative_and_json_with_validation(ex, world):
    g = Graph()
    g.add("logline", id="l", text="Türkçe fikir")
    g.add("image", id="i")
    g.add("output", id="o")
    g.connect("l", "text", "i", "prompt")
    g.connect("i", "image", "o", "in")
    node = g.nodes["i"]
    node.raw = {"prompt": "HAM İSTEM", "negative": "blur, text", "json": '{"cfg": 3.5, "seed": 99, "seed_locked": true}'}
    assert validate_raw(node) == (True, "ok")
    run = ex.run(g)
    body = json.loads([r for r in world.requests if r.url.host == "api.bfl.ai" and r.method == "POST"][-1].content)
    assert body["prompt"] == "HAM İSTEM" and body["seed"] == 99 and run.results["i"].params["cfg"] == 3.5
    node.raw["json"] = "{bozuk"
    ok, msg = validate_raw(node)
    assert not ok and "JSON" in msg
    run = ex.run(g)
    assert run.status == "error" and run.results["i"].status == "error" and run.results["o"].status == "skipped"   # hata → aşağısı atlanır
    node.raw["json"] = "[1]"
    assert validate_raw(node)[0] is False


def test_failure_stops_downstream_and_is_reported_not_raised(ex, world):
    world.down.add("api.bfl.ai")
    g = default_flow()
    run = ex.run(g)
    assert run.status == "error" and run.results["gorsel_01"].status == "error" and run.results["gorsel_01"].error_code.startswith(("net.", "ai."))
    assert run.results["upscale_01"].status == "skipped" and run.results["cikis_01"].status == "skipped" and run.results["logline_01"].status == "done"


def test_cancel_stops_cleanly(ex):
    ex.cancelled = lambda: True
    run = ex.run(default_flow())
    assert run.status == "cancelled" and not run.results


@needs_ffmpeg
def test_batch_matrix_seed_by_cfg_runs_only_this_node(ex, world):
    g = default_flow()
    ex.run(g)
    llm_calls = len([r for r in world.requests if r.url.host == "api.anthropic.com"])
    before = len([r for r in world.requests if r.url.host == "api.bfl.ai" and r.method == "POST"])
    run = ex.run_matrix(g, "gorsel_01", ("seed", [11, 22]), ("cfg", [4.0, 8.0]))
    assert run.mode == "matrix" and run.matrix["x"] == ["seed", [11, 22]] and run.matrix["y"] == ["cfg", [4.0, 8.0]] and len(run.results) == 4
    assert [len(row) for row in run.matrix["cells"]] == [2, 2]
    posts = [json.loads(r.content) for r in world.requests if r.url.host == "api.bfl.ai" and r.method == "POST"][before:]
    assert [p["seed"] for p in posts] == [11, 22, 11, 22]                                                         # X ekseni = seed
    cell = run.results[run.matrix["cells"][1][0]]
    assert cell.params["cfg"] == 8.0 and cell.params["seed"] == 11 and cell.status == "done"
    assert len([r for r in world.requests if r.url.host == "api.anthropic.com"]) == llm_calls                      # yalnız bu düğüm
    assert ex.history[0] is run and run.cost == pytest.approx(0.16)
    with pytest.raises(ValueError):
        ex.run_matrix(g, "gorsel_01", ("seed", list(range(5))), ("cfg", [1, 2, 3, 4, 5]))                          # maliyet sınırı


def test_reference_node_reads_the_series_bible(tmp_path, ex):
    p = SeriesProject()
    p.characters = [Character("a", "Ahmet", "navy coat"), Character("b", "Ayşe", "red scarf")]
    p.locations = [Location("v", "Vapur", "old ferry")]
    ex.project = p
    g = Graph()
    g.add("reference", id="r", character="a", location="v")
    g.add("output", id="o")
    g.connect("r", "text", "o", "in")
    run = ex.run(g)
    text = run.results["r"].outputs["text"]
    assert "Ahmet: navy coat" in text and "Vapur: old ferry" in text and "Ayşe" not in text
    assert Path(run.results["o"].outputs["files"][0]).read_text(encoding="utf-8") == text
