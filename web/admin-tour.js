// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The setup tour: anything that affects user or box security is one of the setup's decisions. The
// setup step links to each setting and highlights it; the clipboard krab is the tour guide.
//
// Each decision has a default already, and the box runs on it until the owner decides: by keeping
// it ("Keep this, next"), or by changing the setting where it lives (any change inside the
// highlighted part counts). The setup step lists them as links; the tour bar steps through them,
// opening the page each lives on and highlighting it. What has been decided is kept on the hub
// (the setting setup_decided). Nothing here is a second copy of a setting.
const TOUR = (() => {
  "use strict";
  // The decisions: words, the section they live in, and the part of it to highlight (a selector).
  // only: a service the step is about, shown only where the box has it (has(), told by admin.js).
  const DECISIONS = [
    {
      id: "visitors",
      label: "Visitor counts",
      section: "security",
      target: "#visitor_counts",
      said: "Default: on. The box counts different devices today and this week. It uses salted hashes and deletes them every day and every week. The box keeps only the counts.",
    },
    {
      id: "names",
      label: "Signed-in name visibility",
      section: "status-tiles",
      target: "#names-to-box",
      said: "Guests see only how many people are signed in. Each person decides whether the box shows their name. New accounts start with the name hidden.",
    },
    {
      id: "signup",
      label: "Account registration",
      section: "accounts",
      target: "#accounts-settings",
      said: "Default: off. The administrator creates the accounts. If you turn registration on, anyone on the box's network can create an account.",
    },
    {
      id: "sign-in-offer",
      label: "Locked tiles",
      section: "apps",
      target: "#sign-in-offer-box",
      said: "Default: off. The tile of an app for users shows only to signed-in users. If you turn this on, guests also see the tile. It has a lock that leads to the sign-in page. The Visibility setting of each app has priority.",
    },
    {
      id: "https",
      label: "HTTP and HTTPS",
      section: "security",
      target: "#security-https",
      said: "Generate or import a certificate, then select HTTP, both transports, HTTPS with HTTP discovery, or HTTPS only. Save applies the choices. Restrictive choices require this page to be opened over HTTPS.",
    },
    {
      id: "root-policy",
      label: "Root helper security policy",
      section: "security",
      target: "#security-policy",
      said: "Initially the hub administrator may change security policy. Console/SSH-only locks those changes at root. Saving that choice means only a root-capable console or SSH session can restore hub access.",
    },
    {
      id: "hotspot",
      label: "Hotspot security",
      section: "network/hotspot",
      fallback: "network",
      target: "#hs-modes",
      said: "Where the radio supports it, the hotspot uses encryption without a password (OWE). Guests join without a password. A person who only listens cannot read their traffic.",
    },
    {
      id: "guest-net",
      label: "Guest internet access",
      section: "network/hotspot",
      fallback: "network",
      target: "#guest-net-box",
      said: "Default: off. Guests on the hotspot reach the box and nothing else. There are five levels. Each level shows what it opens.",
    },
    {
      id: "ssh",
      label: "SSH password authentication",
      section: "security",
      target: "#sec-ssh-password",
      said: "The box keeps the image setting until you change it. If SSH accepts keys only and you have no key, you cannot sign in over SSH.",
    },
    {
      id: "tailscale",
      label: "Remote access (Tailscale)",
      section: "access",
      target: "#remote-section",
      only: "tailscale",
      said: "The box keeps the setting that it found.",
    },
    {
      id: "cockpit",
      label: "Cockpit (root sign-in on port 9090)",
      section: "security",
      target: "#sec-cockpit",
      only: "cockpit",
      said: "While Cockpit runs on the network, every guest can reach its sign-in page.",
    },
    {
      id: "terminal",
      label: "Terminal (shell in the browser)",
      section: "app-access-term",
      fallback: "apps",
      target: "#app-access-term",
      said: "By default, the terminal needs the administrator sign-in. It gives a root shell.",
    },
  ];
  // The setup's own steps, around the decisions, in the same form: what the box is, first; a backup,
  // last. Their state comes from the
  // parts of the page that know it (setupStep in admin.js).
  const OWN = [
    {
      id: "password",
      label: "Administrator password",
      section: "access",
      target: "#password-form",
      said: "Your administrator account signs you in to these pages, the terminal and Syncthing. The box also has its own sign-in for scripts, the user admin. When the first run makes an account, the user admin has no password. Set one here over HTTPS if you use scripts.",
    },
    {
      id: "connection",
      label: "How guests reach the box",
      section: "security",
      target: "#sec-firewall",
      said: "Phones and laptops find the hub through the network that the box is on, or through the box's own hotspot. Two settings decide what a guest on the hotspot reaches: the hotspot firewall, here, and Guest internet access, a later step. Set both.",
    },
    {
      id: "country",
      label: "WiFi country",
      section: "network/access",
      target: "#net-country",
      said: "The country decides which channels and how much power the box's radios can use, and so which phones can see its hotspot. Select the country the box is in. When the operating system has set one already, this step is done.",
    },
    {
      id: "security",
      label: "What the box exposes",
      section: "security",
      target: "#security-findings",
      said: "This step lists what a guest on the network can reach. It lists the settings that change that.",
    },
    {
      id: "addons",
      label: "Add-ons",
      section: "app-inventory",
      target: "#inv-list",
      said: "The hub works without add-ons. Select Available to see the add-ons and apps that this box can add, then add the ones it needs.",
    },
    {
      id: "books",
      label: "Books",
      section: "books",
      target: "#books",
      said: "Kiwix serves the books offline. The librarian can keep them current.",
    },
  ];
  const BACKUP = {
    id: "backup",
    label: "Take a backup",
    section: "backup",
    target: "#backup a[download]",
    said: "Download the box's state now that you have set it up: settings, accounts, saved work and the library sources. Keep the file off the box.",
  };
  const ALL = [...OWN, ...DECISIONS, BACKUP];
  // The services the box has, as admin.js learns them: a step marked only shows once its service is known
  // to be there (Tailscale: /admin/tailscale; Cockpit: the Security page's setting).
  const present = {};
  let STEPS = ALL.filter((step) => !step.only);
  const KIND = Object.fromEntries([
    ...OWN.map((step) => [step.id, "step"]),
    ...DECISIONS.map((decision) => [decision.id, "decision"]),
    [BACKUP.id, "step"],
  ]);
  const GUIDE = "/art/krab-controller-clipboard.webp";
  let decided = new Set();
  let muted = new Set(); // steps the owner asked not to be reminded of (setup_muted): listed, greyed, not counted
  let at = null; // the decision being toured, or null
  // The count waits until the page has said what it knows: the settings, each step's state and whether
  // each only: service is there, or SETTLE_MS, whichever comes first. A part of the page that never
  // reports (its section failed to load) leaves its step "to do", as before.
  const SETTLE_MS = 4000;
  let settingsRead = false;
  let settled = false;
  const settleTimer = setTimeout(() => {
    settled = true;
    drawList();
  }, SETTLE_MS);
  let bar = null;
  let marked = null; // the highlighted element

  const state = (id) =>
    (window.SETUP_STATE ||
      (typeof SETUP_STATE !== "undefined" ? SETUP_STATE : {}))[id];
  // Done: a decision kept or changed; a step its part of the page calls fine, or kept in the tour.
  const isDone = (step) =>
    decided.has(step.id) ||
    (KIND[step.id] === "step" &&
      step.id !== "backup" &&
      (state(step.id) || {}).status === "ok");
  const left = () => STEPS.filter((step) => !isDone(step) && !muted.has(step.id));
  // A step whose state the page hasn't told yet: its part of the page hasn't reported, or (for a decision
  // and the backup) the settings haven't been read.
  const unknown = (step) =>
    !settingsRead ||
    (KIND[step.id] === "step" && step.id !== "backup" && !state(step.id));
  function checking() {
    if (settled) return false;
    if (
      settingsRead &&
      OWN.every((step) => state(step.id)) &&
      ALL.every((step) => !step.only || step.only in present)
    ) {
      settled = true;
      clearTimeout(settleTimer);
    }
    return !settled;
  }
  const PILL = (step, stillChecking) => {
    if (stillChecking && !isDone(step) && unknown(step)) return ["info-pill", "checking"];
    if (KIND[step.id] === "decision")
      return decided.has(step.id)
        ? ["ok-pill", "decided"]
        : muted.has(step.id)
          ? ["info-pill", "no reminders"]
          : ["info-pill", "default"];
    const st = (state(step.id) || {}).status;
    if (isDone(step)) return ["ok-pill", "done"];
    if (muted.has(step.id)) return ["info-pill", "no reminders"];
    return st === "problem"
      ? ["bad-pill", "to fix"]
      : st === "warn"
        ? ["warn-pill", "needs review"]
        : ["info-pill", "to do"];
  };

  async function save() {
    try {
      await postJSON("/admin/settings", { setup_decided: [...decided] });
    } catch (_) {
      /* tried again with the next one */
    }
    drawList();
  }
  async function mute(id, on) {
    if (on) muted.add(id);
    else muted.delete(id);
    try {
      await postJSON("/admin/settings", { setup_muted: [...muted] });
    } catch (_) {
      /* shown as chosen; tried again with the next change */
    }
    drawList();
  }
  let waiting = null; // the step a reload was on, until its service is known
  function has(service, yes) {
    if (present[service] === !!yes) return;
    present[service] = !!yes;
    const now = at != null ? STEPS[at].id : waiting;
    STEPS = ALL.filter((step) => !step.only || present[step.only]);
    const i = now ? STEPS.findIndex((step) => step.id === now) : -1;
    if (at != null) {
      if (i >= 0) {
        at = i;
        show(); // its count in the bar ("3 of 16") follows
      } else stop(); // the step's service has gone
    } else if (waiting && i >= 0) {
      waiting = null;
      at = i;
      show();
    }
    drawList();
  }
  function decide(id) {
    if (decided.has(id)) return;
    decided.add(id);
    save();
  }

  // The setup page: every step in one numbered list, each its short name, its pill, a line of what
  // it is now, and a link that starts the tour there.
  function drawList() {
    if (typeof drawAttention === "function") drawAttention(); // Overview's Needs attention counts what is left
    const list = document.getElementById("setup-steps");
    if (!list) return;
    const stillChecking = checking();
    const n = left().length;
    list.replaceChildren(
      ...STEPS.map((step, i) => {
        const [cls, word] = PILL(step, stillChecking);
        const li = document.createElement("li");
        li.dataset.step = step.id;
        li.className = isDone(step)
          ? "step-ok"
          : muted.has(step.id)
            ? "step-muted"
            : stillChecking && unknown(step)
              ? "step-checking"
              : `step-${(state(step.id) || {}).status || "todo"}`;
        const link = Object.assign(document.createElement("a"), {
          href: "#" + step.section,
          textContent: "Open settings",
        });
        link.dataset.tour = step.id;
        link.addEventListener("click", (event) => {
          event.preventDefault();
          go(i);
        });
        const text = Object.assign(document.createElement("span"), {
          className: "step-text",
          textContent: ((state(step.id) || {}).text || step.said) + " ",
        });
        text.append(link);
        if (!isDone(step)) {
          const m = Object.assign(document.createElement("button"), {
            type: "button",
            className: "link-button mute-step",
            textContent: muted.has(step.id)
              ? "Resume reminders"
              : "Stop reminders",
            title: muted.has(step.id)
              ? "Count this step again"
              : "Keep the step in the list. Do not count it as left to do.",
          });
          m.addEventListener("click", () => mute(step.id, !muted.has(step.id)));
          text.append(" ", m);
        }
        li.append(
          Object.assign(document.createElement("strong"), {
            textContent: step.label,
          }),
          " ",
          Object.assign(document.createElement("span"), {
            className: cls,
            textContent: word,
          }),
          text,
        );
        return li;
      }),
    );
    const said = document.getElementById("setup-left");
    if (said)
      said.textContent = stillChecking
        ? "Checking the steps…"
        : n
          ? `${n} of ${STEPS.length} still to do or decide. Until then the box uses each default.`
          : "All done.";
    const actions = document.querySelector("#welcome .tour-actions");
    if (!actions) return;
    let start = actions.querySelector(".tour-start");
    if (!start) {
      start = Object.assign(document.createElement("button"), {
        type: "button",
        className: "action-btn primary tour-start",
      });
      actions.prepend(start);
    }
    start.disabled = stillChecking;
    start.textContent = stillChecking
      ? "Checking the steps…"
      : !n
        ? "Take the tour again"
        : n === STEPS.length
          ? "Start the tour"
          : "Resume tour";
    start.onclick = () => go(n ? STEPS.indexOf(left()[0]) : 0);
  }

  // The tour: open the decision's page, highlight its part, and the bar below.
  function go(i) {
    if (i == null || i < 0 || i >= STEPS.length) {
      stop();
      return;
    }
    at = i;
    try {
      sessionStorage.setItem("irate-tour", STEPS[i].id); // its id: the steps shown can change (has)
    } catch (_) {
      /* this page's own memory only */
    }
    const step = STEPS[i];
    const section = document.getElementById(step.section)
      ? step.section
      : step.fallback || step.section;
    if (location.hash === "#" + section) show();
    else location.hash = "#" + section;
  }
  // Past the last decision: leave the tour and go back to where it starts (the setup steps, with the list and its start button).
  function finish() {
    stop();
    location.hash = "#welcome";
  }
  function stop() {
    at = null;
    try {
      sessionStorage.removeItem("irate-tour");
    } catch (_) {
      /* nothing kept */
    }
    unmark();
    if (bar) {
      bar.remove();
      bar = null;
    }
  }
  function unmark() {
    if (!marked) return;
    marked.classList.remove("tour-target");
    marked.removeEventListener("change", onChange, true);
    marked.removeEventListener("settings-saved", onChange);
    marked = null;
  }
  function onChange(event) {
    if (event.type === "change" && marked && marked.hasAttribute("data-staged"))
      return;
    if (at != null) decide(STEPS[at].id);
  }
  function show() {
    if (at == null) return;
    const step = STEPS[at];
    unmark();
    let target =
      typeof step.target === "function"
        ? step.target()
        : document.querySelector(step.target);
    if (
      target &&
      target.closest(".settings, .aw-settings, form, .admin-checks > li")
    )
      target = target.closest(".settings, .aw-settings, form, li") || target;
    const here = target && !target.closest("[hidden]");
    if (here) {
      marked = target;
      marked.classList.add("tour-target");
      // Changing it where it lives decides it too.
      marked.addEventListener("change", onChange, true);
      marked.addEventListener("settings-saved", onChange);
      setTimeout(
        () =>
          marked &&
          marked.scrollIntoView &&
          marked.scrollIntoView({ block: "center", behavior: "smooth" }),
        60,
      );
    }
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "tour-bar";
      bar.setAttribute("role", "region");
      bar.setAttribute("aria-label", "Setup tour");
      document.body.append(bar);
    }
    const img = Object.assign(document.createElement("img"), {
      className: "tour-guide",
      src: GUIDE,
      alt: "",
    });
    const text = document.createElement("div");
    text.className = "tour-text";
    text.append(
      Object.assign(document.createElement("strong"), {
        textContent: `Setup tour · ${at + 1} of ${STEPS.length}: ${step.label}`,
      }),
      " ",
      Object.assign(document.createElement("span"), {
        className: PILL(step)[0],
        textContent: PILL(step)[1],
      }),
      Object.assign(document.createElement("p"), {
        className: "setting-desc",
        textContent:
          step.said +
          (here
            ? ""
            : " (This setting is unavailable on this box. Keep the default and continue.)"),
      }),
    );
    const btn = (label, cls, fn) => {
      const b = Object.assign(document.createElement("button"), {
        type: "button",
        className: cls,
        textContent: label,
      });
      b.addEventListener("click", fn);
      return b;
    };
    const back = btn("← Back", "action-btn", () => go(at - 1));
    back.disabled = at === 0;
    const buttons = document.createElement("div");
    buttons.className = "chip-group";
    buttons.append(
      back,
      btn(
        KIND[step.id] === "decision" ? "Keep and continue →" : "Done, continue →",
        "action-btn primary",
        () => {
          decide(step.id);
          if (at + 1 < STEPS.length) go(at + 1);
          else finish();
        },
      ),
      btn("Skip →", "action-btn", () =>
        at + 1 < STEPS.length ? go(at + 1) : finish(),
      ),
      btn("Leave the tour", "action-btn", stop),
    );
    bar.replaceChildren(img, text, buttons);
  }

  async function load() {
    try {
      const st = await getJSON("/admin/settings");
      decided = new Set(st.setup_decided || []);
      muted = new Set(st.setup_muted || []);
    } catch (_) {
      /* the list says what it can */
    }
    settingsRead = true;
    drawList();
    let saved = null;
    try {
      saved = sessionStorage.getItem("irate-tour");
    } catch (_) {
      /* none */
    }
    if (saved != null) {
      // An id, or the index an older page kept; a step whose service is not known yet waits for has().
      const i = /^\d+$/.test(saved)
        ? Number(saved)
        : STEPS.findIndex((step) => step.id === saved);
      if (i >= 0 && i < STEPS.length) {
        at = i;
        show();
      } else if (ALL.some((step) => step.id === saved)) waiting = saved;
    }
  }
  window.addEventListener("hashchange", () => {
    if (at != null) setTimeout(show, 30);
  });
  AL.onBuild(() => {
    if (at != null) setTimeout(show, 30);
    drawList();
  });
  load();
  // Downloading a backup is the backup step done.
  document.addEventListener("click", (event) => {
    if (event.target.closest && event.target.closest("#backup a[download]"))
      decide("backup");
  });
  return {
    DECISIONS,
    get STEPS() {
      return STEPS;
    },
    ALL,
    has,
    go,
    stop,
    decided: () => new Set(decided),
    drawList,
    left: () => (checking() ? null : left().length), // null while the page is still checking
  };
})();
if (typeof window !== "undefined") window.TOUR = TOUR;
