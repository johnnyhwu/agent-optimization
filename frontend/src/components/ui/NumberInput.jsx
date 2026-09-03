import React from "react";

// A number field that does not change its value when you scroll past it.
//
// A focused `<input type="number">` treats the wheel as a spinner. That is a
// reasonable default for a form you are filling in and a bad one for the
// wizard, where a numeric field sits in the middle of a step taller than the
// window: you type an epoch count, reach for the wheel to read the rest of the
// step, and the number under the cursor silently becomes something else. The
// wheel is also swallowed — the page does not move either, so the only sign
// anything happened is a value you did not choose, on a screen that is about to
// spend an hour of paid agent calls.
//
// **`blur()` rather than `preventDefault()`**, and the reason is mechanical:
// React attaches `onWheel` as a passive listener, where `preventDefault()` is
// ignored outright. Attaching a non-passive listener by ref does work, but it
// cancels the *scroll* as well as the increment, which trades one broken
// gesture for another. A number input only responds to the wheel while focused,
// so dropping focus at the start of the gesture makes the rest of it fall
// through to the page — the increment stops and the scroll starts.
//
// The cost is that scrolling while a field is focused takes the focus away.
// That is the right trade here: someone reaching for the wheel is asking to
// move the page, and the value they typed is already in the field.
export default function NumberInput({ onWheel, ...rest }) {
  return (
    <input
      type="number"
      onWheel={(e) => {
        e.currentTarget.blur();
        onWheel?.(e);
      }}
      {...rest}
    />
  );
}
