(function () {
  const state = {
    items: [],
    query: "",
    currentFolderKey: "",
    sort: "title",
    activeId: null,
    hls: null,
  };

  const grid = document.querySelector("#movieGrid");
  const emptyState = document.querySelector("#emptyState");
  const count = document.querySelector("#libraryCount");
  const searchInput = document.querySelector("#searchInput");
  const folderFilter = document.querySelector("#folderFilter");
  const sortSelect = document.querySelector("#sortSelect");
  const rescanButton = document.querySelector("#rescanButton");
  const stopStreamButton = document.querySelector("#stopStreamButton");
  const backButton = document.querySelector("#backButton");
  const viewTitle = document.querySelector("#viewTitle");
  const viewSubtitle = document.querySelector("#viewSubtitle");
  const video = document.querySelector("#videoPlayer");
  const overlay = document.querySelector("#playerOverlay");
  const currentTitle = document.querySelector("#currentTitle");
  const playbackMode = document.querySelector("#playbackMode");
  const statusText = document.querySelector("#statusText");

  video.addEventListener("error", () => {
    playbackMode.textContent = "Error";
    setStatus("Playback failed. Check that the drive is connected and readable.");
  });

  searchInput.addEventListener("input", (event) => {
    state.query = event.target.value.trim().toLowerCase();
    renderLibrary();
  });

  folderFilter.addEventListener("change", (event) => {
    state.currentFolderKey = event.target.value;
    renderLibrary();
  });

  sortSelect.addEventListener("change", (event) => {
    state.sort = event.target.value;
    renderLibrary();
  });

  backButton.addEventListener("click", () => {
    state.currentFolderKey = "";
    folderFilter.value = "";
    renderLibrary();
  });

  rescanButton.addEventListener("click", async () => {
    setStatus("Scanning library...");
    rescanButton.disabled = true;
    try {
      const response = await fetch("/api/rescan", { method: "POST" });
      const data = await response.json();
      state.items = data.items || [];
      if (!getFolderGroups().some((folder) => folder.key === state.currentFolderKey)) {
        state.currentFolderKey = "";
      }
      buildFolderFilter();
      renderLibrary();
      setLibraryStatus(data);
    } catch (error) {
      setStatus(error.message || "Scan failed.");
    } finally {
      rescanButton.disabled = false;
    }
  });

  stopStreamButton.addEventListener("click", async () => {
    if (!state.activeId) {
      return;
    }
    await fetch(`/api/media/${state.activeId}/stop`, { method: "POST" });
    setStatus("Stopped active transcode session.");
    stopStreamButton.disabled = true;
  });

  grid.addEventListener("click", (event) => {
    const folderCard = event.target.closest(".folder-card");
    if (folderCard) {
      state.currentFolderKey = folderCard.dataset.folderKey;
      folderFilter.value = state.currentFolderKey;
      renderLibrary();
      return;
    }

    const movieCard = event.target.closest(".movie-card");
    if (movieCard) {
      playMovie(movieCard.dataset.id);
    }
  });

  async function loadLibrary() {
    setStatus("Loading library...");
    const response = await fetch("/api/library");
    const data = await response.json();
    state.items = data.items || [];
    buildFolderFilter();
    renderLibrary();
    setLibraryStatus(data);
  }

  function buildFolderFilter() {
    const folders = getFolderGroups();
    folderFilter.textContent = "";
    folderFilter.append(new Option("All folders", ""));
    folders.forEach((folder) => {
      folderFilter.append(new Option(folder.displayPath, folder.key));
    });
  }

  function renderLibrary() {
    grid.textContent = "";
    emptyState.hidden = state.items.length > 0;

    if (state.currentFolderKey) {
      renderFolderView();
    } else {
      renderRootView();
    }
  }

  function renderRootView() {
    const folders = getFilteredFolders();
    const looseVideos = getLooseVideos();
    const totalCards = folders.length + looseVideos.length;

    count.textContent = `${state.items.length} ${state.items.length === 1 ? "video" : "videos"}`;
    backButton.hidden = true;
    viewTitle.textContent = "Folders";
    viewSubtitle.textContent =
      totalCards === 0
        ? "No matching folders or videos."
        : `${folders.length} folders and ${looseVideos.length} loose videos`;

    folders.forEach((folder) => grid.append(renderFolderCard(folder)));
    looseVideos.forEach((item) => grid.append(renderMovieCard(item)));
  }

  function renderFolderView() {
    const folder = getFolderGroups().find((group) => group.key === state.currentFolderKey);
    if (!folder) {
      state.currentFolderKey = "";
      folderFilter.value = "";
      renderRootView();
      return;
    }

    const items = sortItems(
      folder.items.filter((item) => {
        if (!state.query) {
          return true;
        }
        return searchableText(item).includes(state.query);
      })
    );

    count.textContent = `${items.length} ${items.length === 1 ? "video" : "videos"}`;
    backButton.hidden = false;
    viewTitle.textContent = folder.label;
    viewSubtitle.textContent = `${folder.displayPath} - ${folder.items.length} videos`;

    items.forEach((item) => grid.append(renderMovieCard(item)));
  }

  function renderFolderCard(folder) {
    const card = document.createElement("button");
    const poster = document.createElement("div");
    const image = document.createElement("img");
    const badge = document.createElement("span");
    const body = document.createElement("span");
    const title = document.createElement("span");
    const meta = document.createElement("span");
    const path = document.createElement("span");

    card.type = "button";
    card.className = "folder-card";
    card.dataset.folderKey = folder.key;

    poster.className = "poster folder-poster";
    image.src = folder.cover.thumbnailUrl;
    image.alt = "";
    image.loading = "lazy";
    image.addEventListener("error", () => image.remove());
    badge.className = "folder-badge";
    badge.textContent = "Folder";
    poster.append(image, badge);

    body.className = "movie-body";
    title.className = "movie-title";
    title.textContent = folder.label;
    meta.className = "movie-meta";
    meta.textContent = `${folder.items.length} ${folder.items.length === 1 ? "video" : "videos"} - ${formatBytes(
      folder.size
    )}`;
    path.className = "movie-folder";
    path.textContent = folder.displayPath;
    body.append(title, meta, path);

    card.append(poster, body);
    return card;
  }

  function renderMovieCard(item) {
    const card = document.createElement("button");
    const poster = document.createElement("div");
    const image = document.createElement("img");
    const body = document.createElement("span");
    const title = document.createElement("span");
    const meta = document.createElement("span");
    const folder = document.createElement("span");

    card.type = "button";
    card.className = "movie-card";
    card.dataset.id = item.id;
    if (item.id === state.activeId) {
      card.classList.add("is-active");
    }

    poster.className = "poster";
    image.src = item.thumbnailUrl;
    image.alt = "";
    image.loading = "lazy";
    image.addEventListener("error", () => image.remove());
    poster.append(image);

    body.className = "movie-body";
    title.className = "movie-title";
    title.textContent = item.title;
    meta.className = "movie-meta";
    meta.textContent = `${item.extension} - ${formatBytes(item.size)}`;
    folder.className = "movie-folder";
    folder.textContent = item.folder;
    body.append(title, meta, folder);

    card.append(poster, body);
    return card;
  }

  function getFolderGroups() {
    const groups = new Map();
    state.items.forEach((item) => {
      if (item.folder === "Root") {
        return;
      }

      const key = folderKey(item);
      if (!groups.has(key)) {
        groups.set(key, {
          key,
          label: folderLabel(item.folder),
          displayPath: `${item.rootName}/${item.folder}`,
          items: [],
          size: 0,
          modified: 0,
          cover: item,
        });
      }

      const group = groups.get(key);
      group.items.push(item);
      group.size += item.size;
      group.modified = Math.max(group.modified, item.modified);
      if (item.modified > group.cover.modified) {
        group.cover = item;
      }
    });

    return sortFolders(Array.from(groups.values()));
  }

  function getFilteredFolders() {
    const folders = getFolderGroups().filter((folder) => {
      if (!state.query) {
        return true;
      }
      const haystack = `${folder.label} ${folder.displayPath} ${folder.items
        .map((item) => item.title)
        .join(" ")}`.toLowerCase();
      return haystack.includes(state.query);
    });
    return sortFolders(folders);
  }

  function getLooseVideos() {
    return sortItems(
      state.items.filter((item) => item.folder === "Root" && (!state.query || searchableText(item).includes(state.query)))
    );
  }

  function sortFolders(folders) {
    return folders.sort((a, b) => {
      if (state.sort === "modified") {
        return b.modified - a.modified;
      }
      if (state.sort === "size") {
        return b.size - a.size;
      }
      return a.displayPath.localeCompare(b.displayPath);
    });
  }

  function sortItems(items) {
    return items.sort((a, b) => {
      if (state.sort === "modified") {
        return b.modified - a.modified;
      }
      if (state.sort === "size") {
        return b.size - a.size;
      }
      if (state.sort === "folder") {
        return `${a.folder} ${a.title}`.localeCompare(`${b.folder} ${b.title}`);
      }
      return a.title.localeCompare(b.title);
    });
  }

  function folderKey(item) {
    return `${item.rootName}::${item.folder}`;
  }

  function folderLabel(folder) {
    const parts = folder.split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] || folder;
  }

  function searchableText(item) {
    return `${item.title} ${item.filename} ${item.folder} ${item.extension}`.toLowerCase();
  }

  async function playMovie(id) {
    const item = state.items.find((entry) => entry.id === id);
    if (!item) {
      return;
    }

    state.activeId = id;
    renderLibrary();
    currentTitle.textContent = item.title;
    overlay.hidden = true;
    playbackMode.textContent = "Preparing";
    stopStreamButton.disabled = true;
    setStatus("Preparing playback...");

    if (state.hls) {
      state.hls.destroy();
      state.hls = null;
    }

    try {
      const playback = await requestPlayback(id);

      if (playback.mode === "direct") {
        video.src = playback.url;
        playbackMode.textContent = "Direct";
        setStatus("Direct playback.");
      } else {
        attachHls(playback.url);
        playbackMode.textContent = "Transcoding";
        stopStreamButton.disabled = false;
        setStatus("Transcoding with FFmpeg.");
      }

      const playRequest = video.play();
      if (playRequest) {
        playRequest.catch(() => setStatus("Press play to start."));
      }
    } catch (error) {
      playbackMode.textContent = "Error";
      setStatus(error.message || "Could not start playback.");
    }
  }

  async function requestPlayback(id) {
    const maxAttempts = 10;
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      const response = await fetch(`/api/media/${id}/playback`);
      const playback = await response.json();

      if (response.ok) {
        return playback;
      }

      if (response.status === 202 && attempt < maxAttempts) {
        playbackMode.textContent = "Preparing";
        setStatus(`Preparing transcode stream... ${attempt}/${maxAttempts}`);
        await sleep(1500);
        continue;
      }

      throw new Error(playback.error || "Playback failed");
    }

    throw new Error("Playback failed");
  }

  function attachHls(url) {
    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = url;
      return;
    }

    if (window.Hls && window.Hls.isSupported()) {
      state.hls = new window.Hls({
        enableWorker: true,
        lowLatencyMode: false,
        backBufferLength: 120,
      });
      state.hls.loadSource(url);
      state.hls.attachMedia(video);
      return;
    }

    throw new Error("This browser needs HLS support for transcoded playback.");
  }

  function setStatus(message) {
    statusText.textContent = message;
  }

  function setLibraryStatus(data) {
    const errors = data.errors || [];
    if (errors.length > 0) {
      setStatus(`Loaded ${state.items.length} videos. Scan warning: ${errors[0]}`);
      return;
    }
    setStatus(`Ready. ${state.items.length} videos loaded.`);
  }

  function sleep(ms) {
    return new Promise((resolve) => {
      window.setTimeout(resolve, ms);
    });
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) {
      return "0 B";
    }
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
  }

  loadLibrary().catch((error) => setStatus(error.message || "Could not load library."));
})();
