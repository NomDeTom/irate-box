# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
r"""The App store's admin side: what /admin/appstore answers (the page in web/admin.js drives it; the runs
it asks for are started by server.py under the librarian's lock, as the firmware page's are).

  snapshot           the holders as the settings keep them: each with its status, the stored survey shown
                     again (its kinds marked all/some/none against the holds as they are now, and a per-release
                     preview derived from the stored names, no network), the files really on disk, the presets,
                     the policy pair and the App store's space
  survey_job         one holder's Look again, run under the lock: read the newest releases, store the survey
  action             one settings action: enable, add-holder (recipe, preset or typed), edit-holder,
                     remove-holder, remove-files, holds (the survey's table turned back into holds)

A stored survey keeps, per kind, only what the page and the matches read (the name it had in each release is
kept; the digests and counters are not), so status.json stays small on the card. Findings belong to the
Updates doctor, not the page ([[findings-in-doctors]]): this says what is; the doctor says what is wrong.
Stdlib only.
"""

from irate_box.hub import refused
from irate_box.library import holder, holdsurvey, librarian
from irate_box.msg import M

VIEW_KEYS = ("window", "short", "dimensions", "eras", "twins")
KIND_KEYS = (
    "shape",
    "class",
    "example",
    "names",
    "releases",
    "seen",
    "size_min",
    "size_max",
    "tags",
    "variant",
    "companions",
)
QUIET = ("checksum or signature", "companion")  # offered beside their parent file, not as ticks of their own


def trim_survey(result):
    """The survey as stored with the holder's status: the parts the page and the holds model read."""
    return {
        **{key: result[key] for key in VIEW_KEYS},
        "kinds": [{key: kind[key] for key in KIND_KEYS if key in kind} for kind in result["kinds"]],
        "surveyed": librarian.now_iso(),
    }


def _preview(stored, holds):
    """Which releases the holds take anything from, and what each takes, as the page shows it: derived from
    the stored names (what each kind was called in each release), so no refetch is needed and nothing the
    survey never saw can be held. {tag: [names]}."""
    taken = {}
    for kind in stored.get("kinds", []):
        for tag, name in kind.get("names", {}).items():
            if holder.held_files({"assets": [{"name": name}]}, {"holds": holds}):
                taken.setdefault(tag, []).append(name)
    return taken


def _size_estimate(stored, holds, keep):
    """About how many bytes one version of the holds costs, and `keep` of them: the newest release the holds
    match, its kinds at their largest size. An estimate is an estimate; the page says "about"."""
    every_tag = sorted({tag for kind in stored.get("kinds", []) for tag in kind.get("names", {})}, reverse=True)
    for tag in every_tag:
        taken = [
            kind
            for kind in stored.get("kinds", [])
            if tag in kind.get("names", {})
            and holder.held_files({"assets": [{"name": kind["names"][tag]}]}, {"holds": holds})
        ]
        if taken:
            once = sum(kind["size_max"] for kind in taken)
            return {"tag": tag, "per_version": once, "total": once * max(0, keep)}
    return {"tag": None, "per_version": 0, "total": 0}


def _survey_view(stored, cfg_holder):
    out = {key: stored[key] for key in VIEW_KEYS if key in stored}
    holds = cfg_holder["holds"]
    states = holdsurvey.held_states(stored, holds)
    kinds = [
        {
            **{key: kind[key] for key in KIND_KEYS if key in kind},
            "state": states.get(kind["shape"], "none"),
            "companions": len(kind.get("companions", [])),
        }
        for kind in stored.get("kinds", [])
        if kind.get("class") not in QUIET
    ]
    out["kinds"] = kinds
    out["own"] = holdsurvey.own_patterns(stored, holds)
    out["preview"] = _preview(stored, holds)
    keep = sum(int(n) for n in cfg_holder["channels"].values()) + sum(
        int(extra.get("keep", 0)) for extra in cfg_holder["extra_channels"]
    )
    out["estimate"] = _size_estimate(stored, holds, keep)
    out["surveyed"] = stored.get("surveyed")
    return out


def snapshot():
    """Everything the App store's page shows, in one answer."""
    cfg = holder.settings()
    policy = librarian.load_config()["policy"]
    holders = []
    for cfg_holder in cfg["holders"]:
        state = holder.holder_status(cfg_holder["id"])
        sizes = holder.disk_uses(cfg_holder["id"])
        stored = state.get("survey")
        holders.append(
            {
                **cfg_holder,
                "status": {
                    key: state.get(key)
                    for key in ("last_check", "outcome", "error", "latest", "surveyed", "survey_error")
                },
                "survey": _survey_view(stored, cfg_holder)
                if isinstance(stored, dict) and stored.get("kinds")
                else None,
                "kept": sizes["versions"],
                "bytes": sizes["bytes"],
            }
        )
    return {
        "enabled": cfg["enabled"],
        "holders": holders,
        "presets": holdsurvey.load_presets(),
        "policy": {key: policy[key] for key in ("holders_every_hours", "holders_auto", "min_free_mb")},
        "free_mb": (librarian._free_bytes(holder.ROOT) >> 20) if holder.ROOT.exists() else None,
        "bytes_all": sum(entry["bytes"] for entry in holders),
        "running": librarian.is_running(),
        "progress": librarian.progress(),
    }


# --- the runs (server.py starts these in the librarian's thread) --------------------------------


def survey_job(holder_id):
    """Look at one holder's newest releases and store the survey. The caller holds the librarian's lock.
    A failure is said of the holder (survey_error in its status), as a failing check is (error): the page
    reads the status again when it polls, so the outcome is not lost with the thread."""
    one = next((h for h in holder.settings()["holders"] if h["id"] == holder_id), None)
    if one is None:
        raise librarian.LibrarianError(M("library.holder.none", "Nothing is kept as {id}.", id=str(holder_id)[:40]))
    try:
        result = holdsurvey.survey(holder.fetch_releases(one["repo"]))
    except (librarian.LibrarianError, OSError, ValueError) as exc:
        holder.patch_status(one["id"], {"survey_error": str(exc)})
        return f"{one['id']}: looked again and failed: {exc}"
    holder.save_survey(one["id"], trim_survey(result))
    return f"{one['id']}: looked at {sum(len(tags) for tags in result['window'].values())} releases"


# --- the settings actions -----------------------------------------------------------------------


def _index_of(holders, holder_id):
    return next((i for i, one in enumerate(holders) if one["id"] == holder_id), None)


def action(payload):
    """(status, body) for a settings action; the body is the whole snapshot after it, so the page redraws
    from the answer as the firmware page does. BUSY for a run is server.py's to answer: it owns the lock."""
    kind = payload.get("action")
    try:
        if kind == "enable":
            holder.set_settings(enabled=bool(payload.get("enabled")))
        elif kind == "add-holder":
            current = holder.settings()["holders"]
            raw = {}
            if payload.get("recipe"):
                raw.update(holdsurvey.recipe_holder(str(payload["recipe"])))
            elif payload.get("preset"):
                raw.update(holdsurvey.find_preset(str(payload["preset"])))
            raw.update({key: payload[key] for key in ("id", "title", "repo", "holds") if key in payload})
            said = raw.pop("notes", None)  # what the recipe asked for that the box does not do (§4): the page says it
            made = holder.validate_holder(raw)
            if _index_of(current, made["id"]) is not None:
                raise librarian.LibrarianError(
                    M("library.holder.id-taken", "There is already one kept called {id}.", id=made["id"])
                )
            holder.set_settings(holders=current + [made])
            if said:
                return 200, {**snapshot(), "notes": said}
        elif kind == "edit-holder":
            current = holder.settings()["holders"]
            index = _index_of(current, payload.get("id"))
            if index is None:
                raise librarian.LibrarianError(
                    M("library.holder.none", "Nothing is kept as {id}.", id=str(payload.get("id"))[:40])
                )
            changed = {
                **current[index],
                **{key: payload[key] for key in _EDITABLE if key in payload},
                "id": current[index]["id"],
            }
            current[index] = holder.validate_holder(changed)
            holder.set_settings(holders=current)
        elif kind == "remove-holder":
            current = holder.settings()["holders"]
            index = _index_of(current, payload.get("id"))
            if index is not None:
                holder.set_settings(holders=[one for i, one in enumerate(current) if i != index])
            holder.remove_files(str(payload.get("id")))
        elif kind == "remove-files":
            holder.remove_files(str(payload.get("id")))
        elif kind == "holds":
            current = holder.settings()["holders"]
            index = _index_of(current, payload.get("id"))
            if index is None:
                raise librarian.LibrarianError(
                    M("library.holder.none", "Nothing is kept as {id}.", id=str(payload.get("id"))[:40])
                )
            one = current[index]
            stored = holder.holder_status(one["id"]).get("survey")
            if not isinstance(stored, dict) or not stored.get("kinds"):
                raise librarian.LibrarianError(
                    M("library.holder.no-survey", "Look at the project's releases first: select Look again.")
                )
            holds = list(one["holds"])
            known = {kind["shape"] for kind in stored.get("kinds", [])}
            for shape, on in (payload.get("kinds") or {}).items():
                if shape not in known:
                    raise librarian.LibrarianError(
                        M(
                            "library.holder.shape-none",
                            "The survey no longer knows a kind of file called {name}.",
                            name=str(shape)[:80],
                        )
                    )
                holds, why = holdsurvey.with_kind(stored, holds, shape, bool(on))
                if why:
                    raise librarian.LibrarianError(why)
            current[index] = holder.validate_holder({**one, "holds": holds})
            holder.set_settings(holders=current)
        else:
            return 400, refused.say(M("hub.action.unknown", "Unknown action. Reload the page and try again."))
    except librarian.LibrarianError as exc:
        return 400, refused.answer(exc)
    return 200, snapshot()


_EDITABLE = (
    "title",
    "repo",
    "holds",
    "channels",
    "extra_channels",
    "tag_pattern",
    "min_age_days",
    "budget_mb",
    "visibility",
)
