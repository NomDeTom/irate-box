// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The box's music: the audio files in the file drop and the podcasts the librarian keeps, as one
// playlist. The browser plays them from the box; no request leaves it.
(() => {
  const list = document.getElementById("music-list");
  const player = document.getElementById("music-player");
  const now = document.getElementById("music-now");
  const nextButton = document.getElementById("music-next");
  const prevButton = document.getElementById("music-prev");
  let tracks = [];
  let playing = null; // the track's id, not its place in the list: a refresh must not move it

  const AUDIO = /\.(mp3|m4a|aac|ogg|oga|opus|wav|flac|webm)$/i;
  const size = (n) => (n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / (1024 * 1024)).toFixed(1)} MB`);
  const dropUrl = (file) => `/api/drop/${encodeURIComponent(file.id)}`;

  function mark() {
    [...list.querySelectorAll("button[data-id]")].forEach((b) => b.classList.toggle("selected", b.dataset.id === playing));
  }

  function play(index) {
    if (!tracks.length) return;
    const at = ((index % tracks.length) + tracks.length) % tracks.length;
    const file = tracks[at];
    playing = file.id;
    player.src = file.url;
    player.play().catch(() => {});
    now.textContent = `${file.name} (${size(file.size)})`;
    mark();
  }

  function played(file) {
    if (file.kind !== "podcast") return;
    fetch("/api/podcasts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ feed: file.feed, id: file.episode, played: true }),
    }).catch(() => {});
  }

  player.addEventListener("ended", () => {
    const at = tracks.findIndex((file) => file.id === playing);
    if (at >= 0) played(tracks[at]);
    play(at + 1);
  });
  nextButton.addEventListener("click", () => {
    const at = tracks.findIndex((file) => file.id === playing);
    play(at + 1);
  });
  prevButton.addEventListener("click", () => {
    const at = tracks.findIndex((file) => file.id === playing);
    play(at - 1);
  });

  async function refresh() {
    try {
      const [drop, casts] = await Promise.all([
        fetch("/api/drop").then((r) => (r.ok ? r.json() : { files: [] })).catch(() => ({ files: [] })),
        fetch("/api/podcasts").then((r) => (r.ok ? r.json() : { episodes: [] })).catch(() => ({ episodes: [] })),
      ]);
      const files = (drop.files || [])
        .filter((file) => AUDIO.test(file.name))
        .map((file) => ({ id: `drop:${file.id}`, name: file.name, size: file.size, url: dropUrl(file), kind: "drop" }));
      const episodes = (casts.episodes || []).map((ep) => ({
        id: `podcast:${ep.feed}:${ep.id}`,
        name: `${ep.feed_title} · ${ep.title}`,
        size: ep.size,
        url: ep.url,
        kind: "podcast",
        feed: ep.feed,
        episode: ep.id,
        starred: ep.starred,
      }));
      tracks = [...episodes, ...files];
      list.replaceChildren(
        ...(tracks.length
          ? tracks.map((file, index) => {
              const item = document.createElement("li");
              const button = document.createElement("button");
              button.type = "button";
              button.className = "action-btn";
              button.dataset.id = file.id;
              button.textContent = `${file.name} (${size(file.size)})`;
              button.addEventListener("click", () => play(index));
              item.append(button);
              if (file.kind === "podcast") {
                const star = document.createElement("button");
                star.type = "button";
                star.className = "small";
                star.textContent = file.starred ? "★ Kept" : "☆ Keep";
                star.title = "A kept episode is never removed by the podcast settings.";
                star.addEventListener("click", async () => {
                  const response = await fetch("/api/podcasts", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ feed: file.feed, id: file.episode, starred: !file.starred }),
                  }).catch(() => null);
                  if (response && response.ok) refresh();
                });
                item.append(star);
              }
              return item;
            })
          : [
              Object.assign(document.createElement("li"), {
                className: "setting-desc",
                textContent: "No audio is on the box yet. Upload files on the File drop, or add a podcast on /admin.",
              }),
            ]),
      );
      if (!tracks.some((file) => file.id === playing)) playing = null;
      mark();
    } catch (_) {
      list.replaceChildren(Object.assign(document.createElement("li"), { className: "setting-desc", textContent: "Could not reach the box." }));
    }
  }

  refresh();
  setInterval(refresh, 15000);
})();
