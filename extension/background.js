// Aqua Browser Bridge: lets Aqua on this PC control YouTube through the page's own player.
// Messages come only from Aqua's native host (com.aqua.browser_bridge); nothing here listens to
// web pages, and every command is checked against a fixed list before it runs.

"use strict";

const HOST = "com.aqua.browser_bridge";
const YOUTUBE = "https://www.youtube.com/*";
const ACTIONS = new Set([
  "play", "pause", "seek", "next", "previous", "fullscreen", "captions", "speed", "quality",
  "volume", "mute", "search", "play_result", "now_playing", "skip_ad",
]);
const MAX_TEXT = 200;

let port = null;
let retryDelay = 1000;

function connect() {
  if (port) return;
  try {
    port = chrome.runtime.connectNative(HOST);
  } catch (error) {
    port = null;
    scheduleRetry();
    return;
  }
  port.onMessage.addListener(onMessage);
  port.onDisconnect.addListener(() => {
    void chrome.runtime.lastError;  // "host not found" until `aqua setup-browser` has run.
    port = null;
    scheduleRetry();
  });
  retryDelay = 1000;
}

function scheduleRetry() {
  setTimeout(connect, retryDelay);
  retryDelay = Math.min(retryDelay * 2, 60000);
}

// The service worker starts on these events; a native port then keeps it running.
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.tabs.onActivated.addListener(connect);
chrome.tabs.onUpdated.addListener((_id, info) => { if (info.status === "complete") connect(); });
connect();

function reply(id, body) {
  if (port) port.postMessage({id, ...body});
}

async function onMessage(message) {
  const id = Number.isInteger(message && message.id) ? message.id : null;
  if (id === null) return;
  try {
    if (message.cmd === "status") {
      reply(id, {ok: true, result: {tab: describe(await newestYouTubeTab())}});
    } else if (message.cmd === "youtube") {
      reply(id, {ok: true, result: await youtube(String(message.action || ""), message.args || {})});
    } else {
      reply(id, {ok: false, error: "unknown command"});
    }
  } catch (error) {
    reply(id, {ok: false, error: String(error && error.message || error).slice(0, MAX_TEXT)});
  }
}

// The most recently active YouTube tab in this browser (Aqua compares browsers).
async function newestYouTubeTab() {
  const tabs = await chrome.tabs.query({url: YOUTUBE});
  let best = null;
  for (const tab of tabs) {
    const used = tab.active ? Math.max(tab.lastAccessed || 0, Date.now() - 1) : (tab.lastAccessed || 0);
    if (!best || used > best.used) best = {tab, used};
  }
  return best;
}

function describe(best) {
  if (!best) return null;
  return {id: best.tab.id, lastAccessed: best.used, title: String(best.tab.title || "").slice(0, MAX_TEXT)};
}

function searchUrl(query) {
  return "https://www.youtube.com/results?search_query=" + encodeURIComponent(String(query).slice(0, 300));
}

async function youtube(action, args) {
  if (!ACTIONS.has(action)) throw new Error("unknown action");
  const best = await newestYouTubeTab();
  if (action === "search") {
    const url = searchUrl(args.query || "");
    if (best) await chrome.tabs.update(best.tab.id, {url, active: true});
    else await chrome.tabs.create({url});
    return {done: "search", query: String(args.query || "").slice(0, 300)};
  }
  if (!best) return {error: "no_tab"};
  const [injection] = await chrome.scripting.executeScript({
    target: {tabId: best.tab.id}, world: "MAIN", func: pageCommand, args: [action, args],
  });
  const result = injection ? injection.result : null;
  if (result && result.fullscreen === "window") {
    // The page may not enter fullscreen without a click; fill the screen with the window instead.
    await chrome.windows.update(best.tab.windowId, {state: "fullscreen"});
  } else if (result && result.fullscreen === "window-off") {
    const win = await chrome.windows.get(best.tab.windowId);
    if (win.state === "fullscreen") await chrome.windows.update(best.tab.windowId, {state: "maximized"});
  }
  return result || {error: "no_result"};
}

// Runs inside the YouTube page (MAIN world) and uses the page's own player API.
// Self-contained: it cannot use anything defined outside this function.
async function pageCommand(action, args) {
  const LIMIT = 200;
  const text = (value) => String(value == null ? "" : value).replace(/[\u0000-\u001f\u007f]/g, " ").slice(0, LIMIT);
  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
  const player = document.getElementById("movie_player");
  const video = document.querySelector("video.html5-main-video") || document.querySelector("video");
  const has = (name) => player && typeof player[name] === "function";
  const QUALITY = {tiny: "144p", small: "240p", medium: "360p", large: "480p", hd720: "720p",
                   hd1080: "1080p", hd1440: "1440p", hd2160: "2160p", highres: "4320p"};
  const now = () => {
    const data = has("getVideoData") ? player.getVideoData() : {};
    const state = has("getPlayerState") ? player.getPlayerState() : (video && !video.paused ? 1 : 2);
    return {
      title: text(data.title || document.title.replace(/ - YouTube$/, "")),
      channel: text(data.author),
      position: Math.round(has("getCurrentTime") ? player.getCurrentTime() : (video ? video.currentTime : 0)),
      duration: Math.round(has("getDuration") ? player.getDuration() : (video ? video.duration || 0 : 0)),
      playing: state === 1,
    };
  };
  const visible = (el) => el && el.offsetParent !== null && getComputedStyle(el).visibility !== "hidden";

  if (action === "now_playing") {
    if (!player && !video) return {error: "no_video"};
    return {done: "now_playing", ...now()};
  }
  if (action === "play_result") {
    const n = Math.max(1, Math.min(20, Number(args.n) || 1));
    let links = [];
    for (let i = 0; i < 25 && links.length < n; i++) {  // Results render a moment after loading.
      links = [...document.querySelectorAll("ytd-search ytd-video-renderer a#video-title")]
        .filter((a) => (a.getAttribute("href") || "").startsWith("/watch"));
      if (links.length < n) await sleep(200);
    }
    if (!location.pathname.startsWith("/results")) return {error: "not_results"};
    if (links.length < n) return {error: "no_result", count: links.length};
    const link = links[n - 1];
    const title = text(link.getAttribute("title") || link.textContent.trim());
    location.assign(new URL(link.getAttribute("href"), location.origin).href);  // Same origin only.
    return {done: "play_result", n, title};
  }
  if (action === "skip_ad") {
    const button = [...document.querySelectorAll(
      ".ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern")].find(visible);
    if (!button) return {error: "no_skip_button"};
    button.click();
    return {done: "skip_ad"};
  }
  if (!player && !video) return {error: "no_video"};
  switch (action) {
    case "play":
      has("playVideo") ? player.playVideo() : await video.play();
      return {done: "play", ...now()};
    case "pause":
      has("pauseVideo") ? player.pauseVideo() : video.pause();
      return {done: "pause", ...now()};
    case "seek": {
      const duration = has("getDuration") ? player.getDuration() : video.duration;
      const current = has("getCurrentTime") ? player.getCurrentTime() : video.currentTime;
      let target = current;
      if (typeof args.by === "number") target = current + args.by;
      else if (typeof args.to === "number") target = args.to;
      else if (typeof args.percent === "number") target = duration * args.percent / 100;
      target = Math.max(0, Math.min(duration ? duration - 1 : target, target));
      has("seekTo") ? player.seekTo(target, true) : (video.currentTime = target);
      return {done: "seek", ...now(), position: Math.round(target)};
    }
    case "next":
      if (!has("nextVideo")) return {error: "unsupported"};
      player.nextVideo();
      return {done: "next"};
    case "previous":
      if (has("previousVideo")) player.previousVideo(); else history.back();
      return {done: "previous"};
    case "fullscreen": {
      const on = args.on !== false;
      if (!on) {
        if (document.fullscreenElement) await document.exitFullscreen().catch(() => {});
        return {done: "fullscreen", on: false, fullscreen: "window-off"};
      }
      if (document.fullscreenElement) return {done: "fullscreen", on: true};
      const button = document.querySelector(".ytp-fullscreen-button");
      if (button) button.click();
      await sleep(400);
      // Browsers only allow page fullscreen after a real click; otherwise fill the screen with the window.
      return {done: "fullscreen", on: true, fullscreen: document.fullscreenElement ? "page" : "window"};
    }
    case "captions": {
      const button = document.querySelector(".ytp-subtitles-button");
      if (!visible(button)) return {error: "no_captions"};
      const on = args.on !== false;
      if ((button.getAttribute("aria-pressed") === "true") !== on) button.click();
      return {done: "captions", on};
    }
    case "speed": {
      const rates = has("getAvailablePlaybackRates") ? player.getAvailablePlaybackRates() : [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
      const wanted = Number(args.rate);
      if (!rates.includes(wanted)) return {error: "unsupported_speed", available: rates};
      has("setPlaybackRate") ? player.setPlaybackRate(wanted) : (video.playbackRate = wanted);
      return {done: "speed", rate: wanted};
    }
    case "quality": {
      if (!has("getAvailableQualityLevels")) return {error: "unsupported"};
      const levels = player.getAvailableQualityLevels().filter((q) => q !== "auto");
      const labels = levels.map((q) => QUALITY[q] || q);
      const wanted = String(args.quality || "").toLowerCase().replace(/\s+/g, "");
      if (wanted === "auto") {
        if (has("setPlaybackQualityRange")) player.setPlaybackQualityRange("auto", "auto");
        return {done: "quality", quality: "auto", available: labels};
      }
      const level = levels.find((q) => (QUALITY[q] || q) === wanted);
      if (!level) return {error: "quality_unavailable", available: labels};
      if (has("setPlaybackQualityRange")) player.setPlaybackQualityRange(level, level);
      else if (has("setPlaybackQuality")) player.setPlaybackQuality(level);
      await sleep(300);
      const current = has("getPlaybackQuality") ? player.getPlaybackQuality() : level;
      return {done: "quality", quality: QUALITY[current] || current, requested: wanted, available: labels};
    }
    case "volume": {
      const level = Math.max(0, Math.min(100, Math.round(Number(args.level))));
      if (Number.isNaN(level)) return {error: "bad_level"};
      if (has("setVolume")) { player.setVolume(level); if (level > 0 && player.isMuted()) player.unMute(); }
      else { video.volume = level / 100; video.muted = level === 0; }
      return {done: "volume", level};
    }
    case "mute": {
      const on = args.on !== false;
      if (has("mute")) { on ? player.mute() : player.unMute(); } else { video.muted = on; }
      return {done: "mute", on};
    }
  }
  return {error: "unknown action"};
}
