const uploadStage = document.getElementById('uploadStage');
const processingStage = document.getElementById('processingStage');
const resultsStage = document.getElementById('resultsStage');

const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const fileChosen = document.getElementById('fileChosen');
const fileChosenName = document.getElementById('fileChosenName');
const analyzeBtn = document.getElementById('analyzeBtn');
const uploadError = document.getElementById('uploadError');
const processingStep = document.getElementById('processingStep');
const startOverBtn = document.getElementById('startOverBtn');
const musicSearchInput = document.getElementById('musicSearchInput');
const musicSearchBtn = document.getElementById('musicSearchBtn');
const musicSearchResults = document.getElementById('musicSearchResults');
const stopAllPreviewsBtn = document.getElementById('stopAllPreviewsBtn');
const downloadStatus = document.getElementById('downloadStatus');
const downloadOutputs = document.getElementById('downloadOutputs');

let selectedFile = null;
let currentJobId = null;
let currentVideoHasAudio = false;
let _cardCounter = 0;

const DEFAULT_API_ORIGIN = 'http://127.0.0.1:8000';

function getConfiguredApiOrigin() {
  if (typeof window !== 'undefined' && typeof window.__VIDEO_MUSIC_API_ORIGIN__ === 'string') {
    return window.__VIDEO_MUSIC_API_ORIGIN__.trim().replace(/\/$/, '');
  }

  // If the file is opened directly or from a different localhost port,
  // default to the API server origin used by this project.
  if (window.location.protocol === 'file:') return DEFAULT_API_ORIGIN;
  const isLocalHost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
  if (isLocalHost && window.location.port && window.location.port !== '8000') {
    return DEFAULT_API_ORIGIN;
  }

  return '';
}

function apiUrl(path) {
  const origin = getConfiguredApiOrigin();
  return origin ? `${origin}${path}` : path;
}

function resolveOutputUrl(url) {
  if (!url) return '';
  if (/^https?:\/\//i.test(url)) return url;
  return apiUrl(url.startsWith('/') ? url : `/${url}`);
}

function getErrorMessage(err, fallback = 'Request failed') {
  if (err && err.name === 'TypeError' && /fetch/i.test(err.message || '')) {
    return 'Cannot reach the API server. Start it on http://127.0.0.1:8000 and try again.';
  }
  return (err && err.message) ? err.message : fallback;
}

const PROCESSING_MESSAGES = [
  'Extracting frames…',
  'Detecting scene cuts…',
  'Reading each scene with vision…',
  'Mapping the emotional arc…',
  'Matching music to the transitions…',
];

function resetUI() {
  closeAllTrackPreviews();
  selectedFile = null;
  currentJobId = null;
  currentVideoHasAudio = false;
  _cardCounter = 0;
  fileInput.value = '';
  fileChosen.hidden = true;
  setError(null);
  downloadStatus.hidden = true;
  downloadStatus.textContent = '';
  downloadOutputs.innerHTML = '';
  musicSearchResults.innerHTML = '';
  musicSearchInput.value = '';
  processingStep.textContent = PROCESSING_MESSAGES[0];
  showStage(uploadStage);
}

function showStage(stage) {
  [uploadStage, processingStage, resultsStage].forEach(s => s.hidden = (s !== stage));
}

function setError(msg) {
  if (!msg) { uploadError.hidden = true; uploadError.textContent = ''; return; }
  uploadError.hidden = false;
  uploadError.textContent = msg;
}

// --- Drag & drop wiring ---
['dragenter', 'dragover'].forEach(evt =>
  dropzone.addEventListener(evt, e => { e.preventDefault(); dropzone.classList.add('dragover'); })
);
['dragleave', 'drop'].forEach(evt =>
  dropzone.addEventListener(evt, e => { e.preventDefault(); dropzone.classList.remove('dragover'); })
);
dropzone.addEventListener('drop', e => {
  const file = e.dataTransfer.files[0];
  if (file) handleFileSelected(file);
});
fileInput.addEventListener('change', e => {
  const file = e.target.files[0];
  if (file) handleFileSelected(file);
});

function isLikelyVideoFile(file) {
  const name = (file.name || '').toLowerCase();
  const extension = name.includes('.') ? name.split('.').pop() : '';
  const videoExtensions = new Set(['mp4', 'mov', 'm4v', 'mkv', 'webm', 'avi', 'wmv', 'flv', 'mpeg', 'mpg', 'ogv']);
  const mimeType = (file.type || '').toLowerCase();
  return mimeType.startsWith('video/') || videoExtensions.has(extension);
}

function handleFileSelected(file) {
  if (!isLikelyVideoFile(file)) {
    setError('That doesn\'t look like a video file. Try MP4, MOV, or WebM.');
    return;
  }
  setError(null);
  selectedFile = file;
  fileChosenName.textContent = `${file.name} — ${(file.size / (1024 * 1024)).toFixed(1)} MB`;
  fileChosen.hidden = false;
}

analyzeBtn.addEventListener('click', () => {
  if (!selectedFile) return;
  runAnalysis(selectedFile);
});

startOverBtn.addEventListener('click', () => {
  resetUI();
});

async function runAnalysis(file) {
  showStage(processingStage);
  let msgIndex = 0;
  processingStep.textContent = PROCESSING_MESSAGES[0];
  const msgInterval = setInterval(() => {
    msgIndex = (msgIndex + 1) % PROCESSING_MESSAGES.length;
    processingStep.textContent = PROCESSING_MESSAGES[msgIndex];
  }, 4500);

  try {
    const formData = new FormData();
    formData.append('file', file);

    const res = await fetch(apiUrl('/api/analyze'), { method: 'POST', body: formData });
    clearInterval(msgInterval);

    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(errBody.detail || `Request failed (${res.status})`);
    }

    const data = await res.json();
    renderResults(data);
    showStage(resultsStage);
  } catch (err) {
    clearInterval(msgInterval);
    showStage(uploadStage);
    setError(`Analysis failed: ${getErrorMessage(err, 'Analysis request failed')}`);
  }
}

// --- Rendering ---

const MOOD_COLOR_MAP = {
  joyful: '#e8a855', happy: '#e8a855', euphoric: '#f0c169',
  tense: '#c96b4f', anxious: '#c96b4f', fearful: '#a4482f',
  melancholic: '#4a9b94', sad: '#3d7a75', somber: '#2f5f5b',
  calm: '#7fa693', peaceful: '#7fa693', serene: '#7fa693',
  energetic: '#e0703e', intense: '#c94f3e', dramatic: '#b5432f',
  romantic: '#c97ba0', warm: '#d99b5c', nostalgic: '#b98f6b',
  neutral: '#6b655c', mysterious: '#5c5a8a',
};

function colorForMood(mood) {
  if (!mood) return '#6b655c';
  const key = mood.toLowerCase().split(/[\s,\/]+/)[0];
  return MOOD_COLOR_MAP[key] || '#8a8478';
}

function formatTime(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}:${rem.toString().padStart(2, '0')}`;
}

function parseTimeInputToSeconds(value) {
  const raw = String(value || '').trim();
  if (!raw) return null;

  if (/^\d+(\.\d+)?$/.test(raw)) {
    return Number(raw);
  }

  const parts = raw.split(':').map(part => part.trim());
  if (parts.some(part => part === '' || Number.isNaN(Number(part)))) {
    return NaN;
  }

  if (parts.length === 2) {
    const [minutes, seconds] = parts.map(Number);
    return (minutes * 60) + seconds;
  }

  if (parts.length === 3) {
    const [hours, minutes, seconds] = parts.map(Number);
    return (hours * 3600) + (minutes * 60) + seconds;
  }

  return NaN;
}

function setDownloadStatus(message, isError = false) {
  if (!message) {
    downloadStatus.hidden = true;
    downloadStatus.textContent = '';
    downloadStatus.classList.remove('download-status--error');
    return;
  }
  downloadStatus.hidden = false;
  downloadStatus.textContent = message;
  downloadStatus.classList.toggle('download-status--error', isError);
}

function renderDownloadOutputs(payload) {
  downloadOutputs.innerHTML = '';
  const entries = [
    { label: 'Download attached song (MP3)', url: payload.track_mp3_url, forceDownload: true },
    { label: 'Downloaded source MP4', url: payload.source_mp4_url },
    { label: 'Download attached video (MP4)', url: payload.merged_video_url, forceDownload: true },
    { label: 'Open attached video (play/stream)', url: payload.merged_video_url },
  ];

  entries.forEach(entry => {
    const link = document.createElement('a');
    link.className = 'download-output-link';
    const resolvedUrl = resolveOutputUrl(entry.url);
    if (entry.forceDownload) {
      link.href = buildForcedDownloadUrl(resolvedUrl);
      link.setAttribute('download', '');
    } else {
      link.href = resolvedUrl;
    }
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = entry.label;
    downloadOutputs.appendChild(link);
  });
}

function buildForcedDownloadUrl(url) {
  const resolvedUrl = resolveOutputUrl(url);
  try {
    const parsed = new URL(resolvedUrl, window.location.origin);
    parsed.searchParams.set('download', '1');
    return parsed.toString();
  } catch {
    return resolvedUrl;
  }
}

function triggerAutoDownload(url, fileName = '') {
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.rel = 'noopener';
  if (fileName) anchor.setAttribute('download', fileName);
  else anchor.setAttribute('download', '');
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  requestAnimationFrame(() => anchor.remove());
}

async function attachTrackToJob(jobId, item, statusNode, clipRange = null, audioOptions = null) {
  setDownloadStatus('Downloading music and attaching it to your video...');
  if (statusNode) statusNode.textContent = 'Processing...';
  try {
    const requestBody = {
      job_id: jobId,
      source_url: item.url,
      source_title: item.title || 'track',
    };

    if (clipRange) {
      requestBody.clip_start_seconds = clipRange.startSeconds;
      requestBody.clip_end_seconds = clipRange.endSeconds;
    }

    if (audioOptions) {
      requestBody.keep_original_audio = audioOptions.keepOriginal;
      requestBody.original_audio_role = audioOptions.originalRole;
    }

    const response = await fetch(apiUrl('/api/music/attach'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody),
    });

    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'Attach failed');

    setDownloadStatus('Music attached successfully. Download links are ready. Click to download when you are ready.');
    renderDownloadOutputs(payload);
    if (statusNode) statusNode.textContent = 'Attached';
  } catch (err) {
    setDownloadStatus(getErrorMessage(err, 'Failed to attach music'), true);
    if (statusNode) statusNode.textContent = 'Failed';
  }
}

function extractYouTubeVideoId(sourceUrl) {
  if (!sourceUrl) return '';
  try {
    const parsed = new URL(sourceUrl);
    const host = parsed.hostname.toLowerCase();

    if (host === 'youtu.be') {
      return parsed.pathname.replace(/^\//, '').split('/')[0] || '';
    }

    if (host.includes('youtube.com')) {
      const watchId = parsed.searchParams.get('v');
      if (watchId) return watchId;

      const pathParts = parsed.pathname.split('/').filter(Boolean);
      const embedIndex = pathParts.findIndex(part => part === 'embed' || part === 'shorts');
      if (embedIndex >= 0 && pathParts[embedIndex + 1]) {
        return pathParts[embedIndex + 1];
      }
    }
  } catch {
    return '';
  }

  return '';
}

function buildYouTubeEmbedUrl(sourceUrl) {
  const videoId = extractYouTubeVideoId(sourceUrl);
  if (!videoId) return '';
  return `https://www.youtube.com/embed/${videoId}?autoplay=1&rel=0&modestbranding=1`;
}

function closeAllTrackPreviews(exceptCard = null) {
  document.querySelectorAll('.youtube-result-card--actionable').forEach(card => {
    if (exceptCard && card === exceptCard) return;
    const preview = card.querySelector('.youtube-preview');
    const frame = card.querySelector('.youtube-preview__frame');
    const toggle = card.querySelector('.youtube-preview-toggle');
    const indicator = card.querySelector('.youtube-preview-indicator');
    if (preview) preview.hidden = true;
    if (frame) frame.src = '';
    if (indicator) indicator.hidden = true;
    if (toggle && !toggle.disabled) toggle.textContent = 'Preview';
  });
  updatePreviewControlsState();
}

function updatePreviewControlsState() {
  if (!stopAllPreviewsBtn) return;
  const anyOpenPreview = Array.from(document.querySelectorAll('.youtube-preview')).some(preview => !preview.hidden);
  stopAllPreviewsBtn.disabled = !anyOpenPreview;
}

function createTrackCard(item, jobId) {
  const card = document.createElement('div');
  card.className = 'youtube-result-card youtube-result-card--actionable';

  const title = document.createElement('span');
  title.className = 'youtube-result-card__title';
  title.textContent = item.title || 'Untitled';

  const meta = document.createElement('span');
  meta.className = 'youtube-result-card__meta';
  meta.textContent = `${item.channel || 'YouTube'}${item.duration ? ` • ${item.duration}` : ''}`;

  const actions = document.createElement('div');
  actions.className = 'youtube-result-card__actions';

  const sectionPicker = document.createElement('div');
  sectionPicker.className = 'music-section-picker';

  const sectionLabel = document.createElement('span');
  sectionLabel.className = 'music-section-picker__label';
  sectionLabel.textContent = 'Attach section';

  const startInput = document.createElement('input');
  startInput.className = 'music-section-picker__input';
  startInput.placeholder = 'start (mm:ss)';
  startInput.inputMode = 'numeric';
  startInput.setAttribute('aria-label', 'Music section start time');

  const endInput = document.createElement('input');
  endInput.className = 'music-section-picker__input';
  endInput.placeholder = 'end (mm:ss)';
  endInput.inputMode = 'numeric';
  endInput.setAttribute('aria-label', 'Music section end time');

  sectionPicker.appendChild(sectionLabel);
  sectionPicker.appendChild(startInput);
  sectionPicker.appendChild(endInput);

  const openLink = document.createElement('a');
  openLink.className = 'btn-ghost-small';
  openLink.href = item.url;
  openLink.target = '_blank';
  openLink.rel = 'noopener';
  openLink.textContent = 'Open source';

  const previewBtn = document.createElement('button');
  previewBtn.type = 'button';
  previewBtn.className = 'btn-ghost-small youtube-preview-toggle';
  previewBtn.textContent = 'Preview';

  const previewWrap = document.createElement('div');
  previewWrap.className = 'youtube-preview';
  previewWrap.hidden = true;

  const previewFrame = document.createElement('iframe');
  previewFrame.className = 'youtube-preview__frame';
  previewFrame.title = `Preview: ${item.title || 'track'}`;
  previewFrame.loading = 'lazy';
  previewFrame.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share';
  previewFrame.referrerPolicy = 'strict-origin-when-cross-origin';
  previewFrame.allowFullscreen = true;

  previewWrap.appendChild(previewFrame);

  const previewIndicator = document.createElement('div');
  previewIndicator.className = 'youtube-preview-indicator';
  previewIndicator.hidden = true;
  previewIndicator.innerHTML = `
    <span class="youtube-preview-indicator__label">Now playing</span>
    <span class="youtube-eq" aria-hidden="true">
      <span class="youtube-eq__bar"></span>
      <span class="youtube-eq__bar"></span>
      <span class="youtube-eq__bar"></span>
      <span class="youtube-eq__bar"></span>
    </span>
  `;

  const embedUrl = buildYouTubeEmbedUrl(item.url);
  if (!embedUrl) {
    previewBtn.disabled = true;
    previewBtn.textContent = 'No preview';
  } else {
    previewBtn.addEventListener('click', () => {
      const willShow = previewWrap.hidden;
      closeAllTrackPreviews(willShow ? card : null);
      if (willShow) {
        previewWrap.hidden = false;
        previewFrame.src = embedUrl;
        previewIndicator.hidden = false;
        previewBtn.textContent = 'Hide preview';
      } else {
        previewWrap.hidden = true;
        previewFrame.src = '';
        previewIndicator.hidden = true;
        previewBtn.textContent = 'Preview';
      }
      updatePreviewControlsState();
    });
  }

  const attachBtn = document.createElement('button');
  attachBtn.className = 'btn-ghost-small';
  attachBtn.textContent = 'Download + attach';

  const status = document.createElement('span');
  status.className = 'youtube-result-card__action';
  status.textContent = '';

  // --- Audio options panel (only shown when the source video has audio) ---
  const cardId = ++_cardCounter;
  const audioPanel = document.createElement('div');
  audioPanel.className = 'audio-options-panel';
  audioPanel.hidden = true;

  let getAudioOptions = () => ({ keepOriginal: false, originalRole: 'background' });

  if (currentVideoHasAudio) {
    const modeName = `audioMode_${cardId}`;
    const roleName = `audioRole_${cardId}`;
    audioPanel.innerHTML = `
      <p class="audio-options-panel__title">This video has existing audio</p>
      <label class="audio-option">
        <input type="radio" name="${modeName}" value="replace" checked />
        Remove original audio — use new music only
      </label>
      <label class="audio-option">
        <input type="radio" name="${modeName}" value="blend" />
        Keep both — blend original audio with new music
      </label>
      <div class="audio-blend-sub" hidden>
        <label class="audio-option audio-option--sub">
          <input type="radio" name="${roleName}" value="music_primary" checked />
          New music is primary &middot; original audio in background
        </label>
        <label class="audio-option audio-option--sub">
          <input type="radio" name="${roleName}" value="original_primary" />
          Original audio is primary &middot; new music in background
        </label>
      </div>
      <div class="audio-options-panel__actions">
        <button type="button" class="btn-ghost-small audio-confirm-btn">Confirm &amp; attach</button>
        <button type="button" class="btn-ghost-small audio-cancel-btn">Cancel</button>
      </div>
    `;

    const blendSub = audioPanel.querySelector('.audio-blend-sub');
    audioPanel.querySelectorAll(`input[name="${modeName}"]`).forEach(input => {
      input.addEventListener('change', () => { blendSub.hidden = input.value !== 'blend'; });
    });

    getAudioOptions = () => {
      const modeEl = audioPanel.querySelector(`input[name="${modeName}"]:checked`);
      const roleEl = audioPanel.querySelector(`input[name="${roleName}"]:checked`);
      return {
        keepOriginal: modeEl ? modeEl.value === 'blend' : false,
        originalRole: roleEl && roleEl.value === 'original_primary' ? 'foreground' : 'background',
      };
    };

    audioPanel.querySelector('.audio-cancel-btn').addEventListener('click', () => {
      audioPanel.hidden = true;
      attachBtn.disabled = false;
    });

    audioPanel.querySelector('.audio-confirm-btn').addEventListener('click', async () => {
      const clipRange = readClipRange();
      if (clipRange === null && (startInput.value.trim() || endInput.value.trim())) return; // validation failed
      const options = getAudioOptions();
      audioPanel.querySelector('.audio-confirm-btn').disabled = true;
      audioPanel.querySelector('.audio-cancel-btn').disabled = true;
      await attachTrackToJob(jobId, item, status, clipRange, options);
      audioPanel.hidden = true;
      audioPanel.querySelector('.audio-confirm-btn').disabled = false;
      audioPanel.querySelector('.audio-cancel-btn').disabled = false;
      attachBtn.disabled = false;
    });
  }

  function readClipRange() {
    const startSeconds = parseTimeInputToSeconds(startInput.value);
    const endSeconds = parseTimeInputToSeconds(endInput.value);
    const hasStart = startInput.value.trim().length > 0;
    const hasEnd = endInput.value.trim().length > 0;
    if (!hasStart && !hasEnd) return null;
    if (!hasStart || !hasEnd) { status.textContent = 'Enter both start and end times.'; return null; }
    if (!Number.isFinite(startSeconds) || !Number.isFinite(endSeconds)) { status.textContent = 'Use time like 1:05 or 65.'; return null; }
    if (startSeconds < 0 || endSeconds <= startSeconds) { status.textContent = 'End time must be after start time.'; return null; }
    status.textContent = `Section ${formatTime(startSeconds)} to ${formatTime(endSeconds)}`;
    return { startSeconds, endSeconds };
  }

  attachBtn.addEventListener('click', async () => {
    const clipRange = readClipRange();
    if (clipRange === null && (startInput.value.trim() || endInput.value.trim())) return;

    if (currentVideoHasAudio) {
      audioPanel.hidden = false;
      attachBtn.disabled = true;
      return;
    }

    attachBtn.disabled = true;
    await attachTrackToJob(jobId, item, status, clipRange, { keepOriginal: false, originalRole: 'background' });
    attachBtn.disabled = false;
  });

  actions.appendChild(openLink);
  actions.appendChild(previewBtn);
  actions.appendChild(attachBtn);
  card.appendChild(title);
  card.appendChild(meta);
  card.appendChild(sectionPicker);
  card.appendChild(actions);
  card.appendChild(audioPanel);
  card.appendChild(previewIndicator);
  card.appendChild(previewWrap);
  card.appendChild(status);
  return card;
}

function renderTrackResults(container, results, jobId, fallbackQuery) {
  container.innerHTML = '';
  if (results && results.length) {
    results.slice(0, 5).forEach(item => {
      container.appendChild(createTrackCard(item, jobId));
    });
    return;
  }

  const fallback = document.createElement('a');
  fallback.className = 'youtube-result-card youtube-result-card--fallback';
  fallback.href = `https://www.youtube.com/results?search_query=${encodeURIComponent(fallbackQuery || '')}`;
  fallback.target = '_blank';
  fallback.rel = 'noopener';
  fallback.textContent = 'No downloader results. Open YouTube search.';
  container.appendChild(fallback);
}

async function runManualMusicSearch() {
  const query = musicSearchInput.value.trim();
  if (!query) return;

  musicSearchBtn.disabled = true;
  musicSearchBtn.textContent = '...';
  setDownloadStatus(null);
  try {
    const response = await fetch(apiUrl('/api/search'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'Search failed');
    renderTrackResults(musicSearchResults, payload.results || [], currentJobId, query);
  } catch (err) {
    setDownloadStatus(getErrorMessage(err, 'Search failed'), true);
  } finally {
    musicSearchBtn.disabled = false;
    musicSearchBtn.textContent = 'Search';
  }
}

function renderResults(data) {
  const { job_id, video_info, overall_analysis, scene_analyses, music_recommendation, youtube_results, recommended_music_candidates } = data;
  currentJobId = job_id;
  currentVideoHasAudio = !!(video_info && video_info.has_audio);
  setDownloadStatus(null);
  downloadOutputs.innerHTML = '';
  musicSearchResults.innerHTML = '';

  document.getElementById('overallTheme').textContent = overall_analysis.overall_theme || '—';
  document.getElementById('dominantMood').textContent = overall_analysis.dominant_mood || '—';
  document.getElementById('pacingValue').textContent = overall_analysis.pacing || '—';
  document.getElementById('bpmValue').textContent = music_recommendation.tempo_bpm_recommendation || '—';
  document.getElementById('idealDurationValue').textContent = music_recommendation.ideal_track_duration || '—';
  document.getElementById('emotionalArc').textContent = overall_analysis.emotional_arc || '';

  // Narrative type + content category tags
  const contentTags = document.getElementById('contentTags');
  contentTags.innerHTML = '';
  [overall_analysis.narrative_type, overall_analysis.content_category].filter(Boolean).forEach(t => {
    const span = document.createElement('span');
    span.className = 'tag tag--meta';
    span.textContent = String(t).replace(/_/g, ' ');
    contentTags.appendChild(span);
  });

  // Trending directions
  const trendingBlock = document.getElementById('trendingBlock');
  const trendingText = music_recommendation.trending_directions;
  if (trendingText) {
    document.getElementById('trendingDirections').textContent = trendingText;
    trendingBlock.hidden = false;
  } else {
    trendingBlock.hidden = true;
  }

  // Filmstrip
  const filmstrip = document.getElementById('filmstrip');
  filmstrip.innerHTML = '';
  scene_analyses.forEach((scene, i) => {
    const block = document.createElement('div');
    block.className = 'scene-block';
    block.tabIndex = 0;
    block.innerHTML = `
      <img src="${apiUrl(`/api/frames/${job_id}/scene_${String(scene.scene_index).padStart(3, '0')}.jpg`)}" alt="Scene ${i + 1} frame" />
      <div class="scene-block__bar" style="background:${colorForMood(scene.mood)}"></div>
      <div class="scene-block__time">${formatTime(scene.start)}</div>
    `;
    block.addEventListener('click', () => showSceneDetail(scene, block));
    filmstrip.appendChild(block);
    if (i === 0) showSceneDetail(scene, block);
  });

  // Transition moments
  const transitionList = document.getElementById('transitionList');
  transitionList.innerHTML = '';
  (overall_analysis.key_transition_moments || []).forEach(t => {
    const li = document.createElement('li');
    li.textContent = t;
    transitionList.appendChild(li);
  });
  if (!(overall_analysis.key_transition_moments || []).length) {
    transitionList.innerHTML = '<li>No major transitions detected — a single continuous mood throughout.</li>';
  }

  // Style tags
  const tagRow = document.getElementById('styleTagRow');
  tagRow.innerHTML = '';
  (music_recommendation.song_style_descriptors || []).forEach(tag => {
    const span = document.createElement('span');
    span.className = 'tag';
    span.textContent = tag;
    tagRow.appendChild(span);
  });

  document.getElementById('musicReasoning').textContent =
    [music_recommendation.reasoning, music_recommendation.reference_artists_or_tracks_style]
      .filter(Boolean).join(' ');

  // Cue sheet
  const cueSheet = document.getElementById('cueSheet');
  cueSheet.innerHTML = '';
  (music_recommendation.section_cue_sheet || []).forEach(cue => {
    const row = document.createElement('div');
    row.className = 'cue-row';
    row.innerHTML = `
      <div class="cue-row__time">${cue.timestamp_range || ''}</div>
      <div class="cue-row__text">${cue.musical_intent || ''}</div>
    `;
    cueSheet.appendChild(row);
  });

  // Suno/Udio prompt
  document.getElementById('sunoPrompt').textContent = music_recommendation.suno_udio_prompt || '';

  // Recommended music shortlist + query buckets
  const ytContainer = document.getElementById('youtubeQueries');
  ytContainer.innerHTML = '';
  updatePreviewControlsState();

  const shortlist = Array.isArray(recommended_music_candidates) && recommended_music_candidates.length
    ? recommended_music_candidates
    : [];

  if (shortlist.length) {
    const shortlistCard = document.createElement('div');
    shortlistCard.className = 'youtube-result-card';
    shortlistCard.innerHTML = '<div class="youtube-result-card__title">Best matches</div>';
    const shortlistList = document.createElement('div');
    shortlistList.className = 'youtube-query-row__results';
    renderTrackResults(shortlistList, shortlist, job_id, '');
    shortlistCard.appendChild(shortlistList);
    ytContainer.appendChild(shortlistCard);
  }

  const queries = music_recommendation.youtube_search_queries || [];
  if (!queries.length && !shortlist.length) {
    const empty = document.createElement('div');
    empty.className = 'youtube-result-card youtube-result-card--fallback';
    empty.textContent = 'No matched tracks were returned. Use "Search other music" below.';
    ytContainer.appendChild(empty);
  }

  queries.forEach(q => {
    const row = document.createElement('div');
    row.className = 'youtube-query-row';
    const resultInfo = youtube_results ? youtube_results[q] : null;

    const text = document.createElement('div');
    text.className = 'youtube-query-row__text';
    text.textContent = q;

    const actions = document.createElement('div');
    actions.className = 'youtube-query-row__actions';

    const resultsList = document.createElement('div');
    resultsList.className = 'youtube-query-row__results';

    renderTrackResults(resultsList, resultInfo && resultInfo.status === 'ok' ? resultInfo.results : [], job_id, q);

    const refreshBtn = document.createElement('button');
    refreshBtn.className = 'btn-ghost-small';
    refreshBtn.textContent = 'Refresh';
    refreshBtn.addEventListener('click', async () => {
      refreshBtn.disabled = true;
      refreshBtn.textContent = '…';
      try {
        const response = await fetch(apiUrl('/api/search'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: q })
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Search failed');

        renderTrackResults(resultsList, payload.results || [], job_id, q);
      } catch (err) {
        setDownloadStatus(getErrorMessage(err, 'Search failed'), true);
      } finally {
        refreshBtn.disabled = false;
        refreshBtn.textContent = 'Refresh';
      }
    });

    actions.appendChild(refreshBtn);
    row.appendChild(text);
    row.appendChild(actions);
    row.appendChild(resultsList);
    ytContainer.appendChild(row);
  });
}

function showSceneDetail(scene, blockEl) {
  document.querySelectorAll('.scene-block.active').forEach(el => el.classList.remove('active'));
  blockEl.classList.add('active');

  const detail = document.getElementById('sceneDetail');
  detail.hidden = false;
  document.getElementById('sceneDetailImg').src = blockEl.querySelector('img').src;
  document.getElementById('sceneDetailTime').textContent = `${formatTime(scene.start)} – ${formatTime(scene.end)}`;
  document.getElementById('sceneDetailMood').textContent = scene.mood || '';
  const desc = [scene.setting, scene.action].filter(Boolean).join(' — ');
  document.getElementById('sceneDetailDesc').textContent = desc;
}

document.getElementById('copyPromptBtn').addEventListener('click', () => {
  const text = document.getElementById('sunoPrompt').textContent;
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById('copyPromptBtn');
    const original = btn.textContent;
    btn.textContent = 'Copied';
    setTimeout(() => { btn.textContent = original; }, 1500);
  });
});

musicSearchBtn.addEventListener('click', runManualMusicSearch);
musicSearchInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    runManualMusicSearch();
  }
});

if (stopAllPreviewsBtn) {
  stopAllPreviewsBtn.addEventListener('click', () => {
    closeAllTrackPreviews();
  });
}

document.addEventListener('DOMContentLoaded', resetUI);
window.addEventListener('pageshow', resetUI);
resetUI();
