// Browser-side tests, evaluated inside frontend/index.html by run.mjs (fresh
// profile, so the library starts empty). The speech service is replaced by a
// fake that returns one timing per word (WORD_MS apart) and a silent WAV of
// matching length, so playback, storage and timing behaviour are tested
// without the network or the Python server.
(async () => {
  const results = [];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (fn, ms = 15000) => {
    const start = performance.now();
    while (!fn()) {
      if (performance.now() - start > ms) throw new Error(`timed out waiting for: ${fn}`);
      await sleep(20);
    }
  };
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  async function test(name, fn) {
    try {
      await fn();
      results.push({ name, ok: true });
    } catch (error) {
      results.push({ name, ok: false, detail: error.message });
    }
    // leave nothing playing or waiting for the next test
    if (equationHold) pauseEquationHold();
    wantsToPlay = false;
    waitingFor = null;
    audioPlayer.pause();
    await waitForPreparationIdle();
    await sleep(100);
  }
  const word = () => globalWordIndex(getCurrentWordIndexForPosition());

  // --- Controlled fixtures: silent audio and a fake speech service ----------
  const WORD_MS = 60;
  function silentWav(ms) {
    const rate = 8000, samples = Math.ceil((rate * ms) / 1000);
    const view = new DataView(new ArrayBuffer(44 + samples * 2));
    const text = (offset, s) => [...s].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
    text(0, "RIFF"); view.setUint32(4, 36 + samples * 2, true); text(8, "WAVE"); text(12, "fmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true);
    view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, samples * 2, true);
    return new Blob([view.buffer], { type: "audio/wav" });
  }
  const service = { calls: [], delayMs: 0, durations: new Map() };
  runSynthesis = async (text) => {
    service.calls.push(text);
    if (service.delayMs) await sleep(service.delayMs);
    const words = textWords(text).map((w, i) => ({ word: w.word, start_ms: 100 + i * WORD_MS, end_ms: 100 + i * WORD_MS + WORD_MS - 15 }));
    const url = `fixture:${service.calls.length}`;
    service.durations.set(url, 100 + words.length * WORD_MS + 300);
    return { audio_url: url, words };
  };
  fetchAudioBlob = async (url) => silentWav(service.durations.get(url));
  const documentText = (label, sentences) =>
    Array.from({ length: sentences }, (_, i) => `${label} sentence ${i + 1} has a few plain words.`).join(" ");
  async function newDocument(name, text) {
    await synthesize(name, text, "text");
    await until(() => !audioPlayer.paused);
    togglePlayPause();
    await waitForPreparationIdle();
    return player.documentId;
  }
  // Plays from a word, then pauses as the reader would (saves the position).
  async function readFrom(globalWord) {
    playFromWord(globalWord);
    await until(() => !audioPlayer.paused && !sectionLoading);
    await sleep(150);
    togglePlayPause();
    await sleep(200);
  }

  // --- Legacy migration (must run first: it rebuilds the database) ----------
  await test("legacy migration keeps an entry's position, audio and timings", async () => {
    // A version-1 entry stored everything in one record; after the upgrade it
    // must be one document plus one ready section with the same audio, and
    // the saved position must index the same words.
    const audio = silentWav(2000);
    const timings = ["Old", "entry", "with", "saved", "audio."].map((w, i) => ({ word: w, start_ms: 100 + i * 300, end_ms: 350 + i * 300 }));
    (await openLibraryDB()).close();
    libraryDBPromise = null;
    await new Promise((resolve, reject) => {
      const r = indexedDB.deleteDatabase(LIBRARY_DB_NAME);
      r.onsuccess = resolve; r.onerror = reject;
    });
    await new Promise((resolve, reject) => {
      const r = indexedDB.open(LIBRARY_DB_NAME, 1);
      r.onupgradeneeded = () => r.result.createObjectStore("documents", { keyPath: "id", autoIncrement: true });
      r.onsuccess = () => {
        const tx = r.result.transaction("documents", "readwrite");
        tx.objectStore("documents").add({ name: "Legacy", text: "Old entry with saved audio.", words: timings, audioBlob: audio, positionWordIndex: 3, lastOpenedAt: 1 });
        tx.oncomplete = () => { r.result.close(); resolve(); };
        tx.onerror = reject;
      };
      r.onerror = reject;
    });
    const [doc] = await getAllLibraryDocuments();
    const sections = await getDocumentSections(doc);
    check(doc.schema === 2 && doc.name === "Legacy", "not migrated to a version-2 document");
    check(doc.positionWordIndex === 3 && doc.words[3].word === "saved", "position no longer points at the same word");
    check(sections.length === 1 && sections[0].status === SECTION_READY, "no ready section");
    check(sections[0].audioBlob.size === audio.size, "audio not carried over");
    check(sections[0].timings.map((t) => t.word).join(" ") === "Old entry with saved audio.", "timings not carried over");
    const before = service.calls.length;
    await reopenLibraryEntry(doc.id);
    await until(() => !audioPlayer.paused);
    check(word() >= 3, `resumed at word ${word()}, not the saved word 3`);
    check(service.calls.length === before, "reopening a migrated entry requested new audio");
    await deleteLibraryDocument(doc.id);
  });

  // --- Library --------------------------------------------------------------
  const textA = documentText("Apple", 260); // ~2,340 words: five sections
  const textB = documentText("Birch", 90);
  let docA, docB;

  await test("a named document is saved once, with its sections partitioned", async () => {
    // One library entry per document; only the first section has audio, and
    // its audio and timings are stored together in one record.
    docA = await newDocument("Doc A", textA);
    const docs = await getAllLibraryDocuments();
    check(docs.filter((d) => d.name === "Doc A").length === 1, "not exactly one entry");
    const sections = await getDocumentSections(docs.find((d) => d.id === docA));
    check(sections.length >= 2, "long text should have several sections");
    const ready = sections.filter((s) => s.status === SECTION_READY);
    check(ready.length >= 1 && ready.every((s) => s.audioBlob && s.timings.length > 0), "ready sections need audio and timings together");
    check(sections.filter((s) => s.status !== SECTION_READY).every((s) => !s.audioBlob && s.timings.length === 0), "missing sections must have neither");
  });

  await test("position is saved and the document reopens at that word", async () => {
    // Pausing saves the document-wide word; reopening resumes there.
    await readFrom(200);
    const saved = (await getLibraryDocument(docA)).positionWordIndex;
    check(saved >= 200 && saved < 215, `saved position ${saved}, expected just after 200`);
    await reopenLibraryEntry(docA);
    await until(() => !audioPlayer.paused);
    check(Math.abs(word() - saved) <= 2, `reopened at ${word()}, saved ${saved}`);
  });

  await test("two documents keep independent positions", async () => {
    // Reading one document must never move the other's saved place.
    docB = await newDocument("Doc B", textB);
    await readFrom(150);
    const b = (await getLibraryDocument(docB)).positionWordIndex;
    await reopenLibraryEntry(docA);
    await until(() => !audioPlayer.paused);
    await readFrom(40);
    const a = (await getLibraryDocument(docA)).positionWordIndex;
    const bNow = (await getLibraryDocument(docB)).positionWordIndex;
    check(a < 60 && bNow === b, `positions mixed up: A saved ${a}; B was ${b}, now ${bNow}`);
    check(b >= 150 && b < 165, `B saved at ${b}`);
  });

  await test("rename and delete from the library list", async () => {
    // Uses the list's own buttons. Delete removes the document and every one
    // of its sections.
    await renderLibrary();
    const row = () => [...document.querySelectorAll(".library-entry")].find((r) => r.textContent.includes("Doc B") || r.querySelector("input"));
    const button = (label) => [...row().querySelectorAll("button")].find((b) => b.textContent === label);
    button("Rename").click();
    await sleep(100);
    row().querySelector("input").value = "Doc B renamed";
    button("Save").click();
    await sleep(300);
    check((await getLibraryDocument(docB)).name === "Doc B renamed", "rename not saved");
    check((await getLibraryDocument(docA)).name === "Doc A", "rename touched another entry");
    button("Delete").click();
    await sleep(300);
    check(!(await getLibraryDocument(docB)), "document still stored");
    check((await getAllSections()).every((s) => s.documentId !== docB), "its sections are still stored");
    check(!!(await getLibraryDocument(docA)), "deleted the wrong entry");
  });

  // --- Sections ---------------------------------------------------------------
  await test("sections cover the text exactly once", async () => {
    // Every word belongs to exactly one section: contiguous ranges, no gaps,
    // no overlaps. The opening sections are short (about 60, then 200 words)
    // so audio starts fast; the rest are 300-600 words each.
    const doc = await getLibraryDocument(docA);
    const sections = (await getDocumentSections(doc)).sort((x, y) => x.wordStart - y.wordStart);
    check(validateSections(sections, doc.words.length).length === 0, validateSections(sections, doc.words.length).join("; "));
    check(sections[0].wordStart === 0 && sections.at(-1).wordEnd === doc.words.length, "doesn't span the document");
    sections.forEach((s, i) => {
      if (i) check(s.wordStart === sections[i - 1].wordEnd, `gap or overlap before section ${i}`);
      const target = OPENING_SECTION_TARGETS[i] ?? SECTION_TARGET_WORDS;
      const size = s.wordEnd - s.wordStart;
      check(size >= Math.round((target * 2) / 3) && size <= Math.round((target * 4) / 3), `section ${i} has ${size} words`);
    });
  });

  await test("a later section's timings map to document-wide words", async () => {
    // Captions, position and jumps use global words: a timing in section 1
    // must point at the document word it speaks.
    await reopenLibraryEntry(docA, { browseOnly: true });
    playFromWord(player.sections[1].wordStart + 5);
    await until(() => !audioPlayer.paused && activeIndex === 1);
    togglePlayPause();
    const section = player.sections[1];
    section.timings.forEach((t, local) => {
      check(t.wordIndex >= section.wordStart && t.wordIndex < section.wordEnd, `timing ${local} outside its section`);
      check(player.words[t.wordIndex].word === t.word, `timing ${local} "${t.word}" ≠ word ${t.wordIndex} "${player.words[t.wordIndex].word}"`);
    });
    const target = section.wordStart + 37;
    const place = locateWord(target);
    check(place.index === 1 && section.timings[place.timing].wordIndex === target, "locateWord didn't find the word's own timing");
  });

  await test("a range across a section boundary stops exactly at its end word", async () => {
    // Plays across the boundary, pauses right after the end word, and never
    // prepares anything past the section the range ends in.
    const boundary = player.sections[1].wordStart;
    selectionStartIndex = boundary - 4;
    selectionEndIndex = boundary + 3;
    applySelectionHighlight();
    playSelectionButton.click();
    await until(() => !audioPlayer.paused);
    await until(() => audioPlayer.paused && !sectionLoading && selectionStopWord === null);
    await sleep(200);
    check(word() === boundary + 3, `stopped at ${word()}, not ${boundary + 3}`);
    check(activeIndex === 1, "didn't cross into the next section");
    check(wantedSections().every((i) => i <= 1), "prepares sections past the range");
    clearSelection();
  });

  // --- Stale work must never override the reader ------------------------------
  await test("a pause during a section load wins over the load", async () => {
    // The reader pauses while a section is still loading: when it loads, it
    // must stay paused.
    playFromWord(player.sections[0].wordStart + 10);
    check(sectionLoading, "expected the load to still be pending");
    togglePlayPause();
    await until(() => !sectionLoading);
    await sleep(300);
    check(audioPlayer.paused, "playback started after the reader paused");
  });

  await test("a pause while waiting for a slow section wins when it arrives", async () => {
    // Audio for a jump is still being prepared and the reader pauses: when it
    // arrives it's ready to play but doesn't start.
    service.delayMs = 800;
    const missing = player.sections.findLastIndex((s, i) => !isReady(i));
    playFromWord(player.sections[missing].wordStart);
    await sleep(100);
    togglePlayPause();
    await until(() => isReady(missing), 10000);
    await sleep(300);
    service.delayMs = 0;
    check(audioPlayer.paused, "played after the reader paused");
    check(preparingNoticeText.textContent.startsWith("Ready."), "notice should say it's ready");
  });

  await test("switching documents with a request in flight never plays the old result", async () => {
    // A slow request for document A finishes after document C is open: it's
    // saved for A, but the player, its audio and its sections stay C's.
    service.delayMs = 800;
    await reopenLibraryEntry(docA, { browseOnly: true });
    const missing = player.sections.findLastIndex((s, i) => !isReady(i));
    const targetStart = player.sections[missing].wordStart;
    playFromWord(targetStart);
    await until(() => inFlight && inFlight.index === missing);
    service.delayMs = 0;
    const docC = await newDocument("Doc C", documentText("Cedar", 40));
    const audioOfC = audioPlayer.src;
    await sleep(1200);
    check(player.documentId === docC, "player switched back to the old document");
    check(audioPlayer.src === audioOfC && audioPlayer.paused, "old result touched C's audio");
    const sectionsA = await getDocumentSections(await getLibraryDocument(docA));
    const target = sectionsA.find((s) => s.wordStart === targetStart);
    check(target && target.status === SECTION_READY && target.audioBlob, "A's result wasn't saved for A");
  });

  // --- Equation timers ------------------------------------------------------
  // A PDF document whose equation follows word 20 (holds ~1.4 s in).
  const pdfText = documentText("Delta", 50);
  const pdfWords = textWords(pdfText).map((w, i) => ({ ...w, page: Math.floor(i / 100) + 1 }));
  const pdfId = await createPdfDocument("Equation doc", {
    text: pdfText, words: pdfWords, page_count: 4, equations: [{ text: "x = y + z", page: 1, after_word: 20, inline: false }],
  });
  async function holdAtEquation() {
    await reopenLibraryEntry(pdfId);
    playFromWord(15);
    try {
      await until(() => !!equationHold, 10000);
    } catch (error) {
      // seen once in ~20 runs: report the player's state so the cause shows
      throw new Error(`${error.message}; state: word ${word()}, paused ${audioPlayer.paused}, loading ${sectionLoading}, `
        + `waiting ${!!waitingFor}, armedFrom ${equationArmFrom}, fired [${[...firedEquations]}], onScreen ${equationOnScreen}`);
    }
  }

  await test("an equation pauses, shows its text and resumes after 5 s", async () => {
    // Baseline for the timer tests: hold, equation on screen, focus mode on,
    // then playback carries on from the next word on its own.
    await holdAtEquation();
    check(wordDisplay.textContent === "x = y + z" && audioPlayer.paused, "equation not shown, or audio still playing");
    check(document.body.classList.contains("focus-mode"), "focus mode left during a programmatic pause");
    await sleep(5300);
    check(!audioPlayer.paused && word() >= 21, `didn't resume after the equation (word ${word()})`);
  });

  await test("a user pause during an equation stops the auto-resume", async () => {
    // The 5 s timer must not restart playback the reader paused.
    await holdAtEquation();
    togglePlayPause();
    await sleep(5500);
    check(audioPlayer.paused, "the equation timer resumed paused playback");
    check(wordDisplay.textContent === "x = y + z", "equation should stay on screen while paused");
  });

  await test("a document switch during an equation stops its timer", async () => {
    // The old document's timer must not resume anything after a switch.
    await holdAtEquation();
    await reopenLibraryEntry(docA, { browseOnly: true });
    await sleep(5500);
    check(audioPlayer.paused && player.documentId === docA && activeIndex === -1, "old equation timer resumed playback");
    check(!wordDisplay.classList.contains("equation"), "old equation still on screen");
  });

  await test("a seek during an equation stops its timer", async () => {
    // Jumping elsewhere cancels the hold; 5 s later nothing jumps back.
    await holdAtEquation();
    playFromWord(60);
    await until(() => !audioPlayer.paused);
    togglePlayPause();
    const at = word();
    await sleep(5500);
    check(audioPlayer.paused && word() === at, "a stale equation timer moved or restarted playback");
  });

  return results;
})();
