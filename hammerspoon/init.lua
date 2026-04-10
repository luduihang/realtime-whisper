-- Wisper / realtime-whisper: global hotkey + local ASR daemon
--
-- Hotkeys:
--   Cmd+Shift+Space : toggle flag (debug / UX)
--   Option+V        : toggle ASR start/stop (stream partials into focused field, final on stop)
--   Cmd+Shift+V     : type a test string (verify Accessibility injection)
--
-- Requires: python asr_daemon.py on http://127.0.0.1:8765

hs.console.clearConsole()
hs.alert.show("Hammerspoon loaded")

local isRecording = false
local daemonBase = "http://127.0.0.1:8765"

local partialPollTimer = nil
local lastSeenSeq = 0
local lastTyped = ""

local function notify(msg)
  hs.alert.closeAll()
  hs.alert.show(msg, 1.0)
  print(os.date("%Y-%m-%d %H:%M:%S") .. "  " .. msg)
end

local function utf8Len(s)
  if not s or s == "" then return 0 end
  if type(utf8) == "table" and utf8.len then
    return utf8.len(s)
  end
  local _, n = s:gsub("[%z\1-\127\194-\244][\128-\191]*", "")
  return n
end

local function replaceTyped(prev, nextText)
  if prev == nextText then return end
  local n = utf8Len(prev)
  for _ = 1, n do
    hs.eventtap.keyStroke({}, "delete")
  end
  if nextText ~= nil and nextText ~= "" then
    hs.eventtap.keyStrokes(nextText)
  end
end

local function stopPartialPoll()
  if partialPollTimer then
    partialPollTimer:stop()
    partialPollTimer = nil
  end
end

local function tickPartial()
  if not isRecording then return end
  hs.http.doAsyncRequest(daemonBase .. "/partial", "GET", nil, nil, function(status, body, headers)
    if not isRecording then return end
    if status ~= 200 then return end
    local ok, obj = pcall(hs.json.decode, body or "")
    if not ok or not obj then return end
    local seq = obj.seq or 0
    local text = obj.text or ""
    if seq <= lastSeenSeq then return end
    lastSeenSeq = seq
    replaceTyped(lastTyped, text)
    lastTyped = text
  end)
end

--- @param reset boolean if true, clear seq/text tracking (new session)
local function startPartialPoll(reset)
  stopPartialPoll()
  if reset then
    lastSeenSeq = 0
    lastTyped = ""
  end
  partialPollTimer = hs.timer.doEvery(0.2, tickPartial)
end

hs.hotkey.bind({"cmd", "shift"}, "space", function()
  isRecording = not isRecording
  if isRecording then
    notify("ASR: START (hotkey detected)")
  else
    notify("ASR: STOP (hotkey detected)")
  end
end)

hs.hotkey.bind({"alt"}, "v", function()
  if not isRecording then
    isRecording = true
    notify("ASR: START…")
    hs.http.doAsyncRequest(daemonBase .. "/start", "POST", "", nil, function(status, body, headers)
      if status ~= 200 then
        isRecording = false
        stopPartialPoll()
        notify("ASR start failed: HTTP " .. tostring(status))
        return
      end
      startPartialPoll(true)
      notify("ASR: recording")
    end)
  else
    isRecording = false
    stopPartialPoll()
    notify("ASR: STOP…")
    hs.http.doAsyncRequest(daemonBase .. "/stop", "POST", "", nil, function(status, body, headers)
      if status ~= 200 then
        isRecording = true
        startPartialPoll(false)
        notify("ASR stop failed: HTTP " .. tostring(status))
        return
      end
      local ok, obj = pcall(hs.json.decode, body or "")
      if not ok or not obj then
        isRecording = true
        startPartialPoll(false)
        notify("ASR stop failed: bad JSON")
        return
      end
      local finalText = obj.finalText or ""
      replaceTyped(lastTyped, finalText)
      lastTyped = ""
      lastSeenSeq = 0
      notify("ASR: done")
    end)
  end
end)

hs.hotkey.bind({"cmd", "shift"}, "v", function()
  notify("Typing test string…")
  hs.eventtap.keyStrokes("[wisper] hotkey OK, keyStrokes OK. ")
end)
