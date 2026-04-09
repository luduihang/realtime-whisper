-- Hammerspoon hotkey smoke test for this project
-- Goal (phase 1): verify global hotkey detection and keystroke injection.
--
-- Hotkeys:
--   Cmd+Shift+Space : toggle "recording" flag (for now just alert/log)
--   Cmd+Shift+V     : type a test string into the current focused input
--
-- Next step after this works:
--   - on start: call local Python daemon /start
--   - on stop : call /stop and inject finalText once

hs.console.clearConsole()
hs.alert.show("Hammerspoon loaded")

local isRecording = false

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

-- Keystroke injection test
hs.hotkey.bind({"cmd", "shift"}, "v", function()
  notify("Typing test string…")
  hs.eventtap.keyStrokes("[wisper] hotkey OK, keyStrokes OK. ")
end)

