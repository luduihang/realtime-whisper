-- Hammerspoon hotkey smoke test for this project
-- Goal (phase 1): verify global hotkey detection and keystroke injection.
--
-- Hotkeys:
--   Cmd+Shift+Space : toggle "recording" flag (for now just alert/log)
--   Option+V        : toggle ASR start/stop (phase 1, inject final once)
--   Cmd+Shift+V     : type a test string into the current focused input
--
-- Next step after this works:
--   - on start: call local Python daemon /start
--   - on stop : call /stop and inject finalText once

hs.console.clearConsole()
hs.alert.show("Hammerspoon loaded")

local isRecording = false
local daemonBase = "http://127.0.0.1:8765"

local function notify(msg)
  hs.alert.closeAll()
  hs.alert.show(msg, 1.0)
  print(os.date("%Y-%m-%d %H:%M:%S") .. "  " .. msg)
end

-- Toggle hotkey (detection only for now)
hs.hotkey.bind({"cmd", "shift"}, "space", function()
  isRecording = not isRecording
  if isRecording then
    notify("ASR: START (hotkey detected)")
  else
    notify("ASR: STOP (hotkey detected)")
  end
end)

-- Phase 1: toggle daemon start/stop, inject finalText once.
hs.hotkey.bind({"alt"}, "v", function()
  if not isRecording then
    isRecording = true
    notify("ASR: START…")
    hs.http.doAsyncRequest(daemonBase .. "/start", "POST", "", nil, function(status, body, headers)
      if status ~= 200 then
        isRecording = false
        notify("ASR start failed: HTTP " .. tostring(status))
        return
      end
      notify("ASR: recording")
    end)
  else
    isRecording = false
    notify("ASR: STOP…")
    hs.http.doAsyncRequest(daemonBase .. "/stop", "POST", "", nil, function(status, body, headers)
      if status ~= 200 then
        notify("ASR stop failed: HTTP " .. tostring(status))
        return
      end
      local ok, obj = pcall(hs.json.decode, body or "")
      if not ok or not obj then
        notify("ASR stop failed: bad JSON")
        return
      end
      local text = obj["finalText"] or ""
      if text ~= "" then
        hs.eventtap.keyStrokes(text)
      end
      notify("ASR: done")
    end)
  end
end)

-- Keystroke injection test (kept for quick debugging)
hs.hotkey.bind({"cmd", "shift"}, "v", function()
  notify("Typing test string…")
  hs.eventtap.keyStrokes("[wisper] hotkey OK, keyStrokes OK. ")
end)

