"""v0.6 track D, task 3: the Motion tab now exposes an Exit row and a
speed (duration_ms) control for both Entrance and Exit. Neither
`styles_adapter.py` nor `routes_styles.py` changed for this -- `exit` and
`entrance.duration_ms` already round-tripped through PUT/GET (schema.py
had them since v0.3); only the UI was blind to them. This test guards
that fact against ever regressing when the JS is touched again: PUT a
style with non-default values for all three fields the new UI controls,
then GET it back and confirm they survive exactly."""
from __future__ import annotations

from .fakes import default_style_definition


def test_exit_and_duration_survive_save_and_reload(client):
    definition = default_style_definition("MOTION TEST")
    definition["entrance"] = {"effect": "slide", "duration_ms": 340}
    definition["exit"] = {"effect": "rise", "duration_ms": 275}

    put_res = client.put("/api/styles/MOTION%20TEST", json=definition)
    assert put_res.status_code == 200
    assert put_res.json()["definition"]["entrance"] == {"effect": "slide", "duration_ms": 340}
    assert put_res.json()["definition"]["exit"] == {"effect": "rise", "duration_ms": 275}

    get_res = client.get("/api/styles/MOTION%20TEST")
    assert get_res.status_code == 200
    body = get_res.json()["definition"]
    assert body["entrance"] == {"effect": "slide", "duration_ms": 340}
    assert body["exit"] == {"effect": "rise", "duration_ms": 275}
