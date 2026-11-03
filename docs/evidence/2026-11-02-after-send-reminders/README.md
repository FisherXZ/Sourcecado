# After-send reminder component evidence

These are component screenshots using synthetic Maya/Acme data, the production
React component and Warm Operator styles. The local browser fixture intercepted
every reminder request; no Gmail, Apollo, calendar or real operator state was
used. They show the optional action, chosen date/text and saved task at widths
1440 and 390. The narrow form has no horizontal overflow and uses 44px controls.

| State | 1440px | 390px |
|---|---|---|
| Optional action after confirmed send | [Offer](offer-1440.png) | [Offer](offer-390.png) |
| Director-selected date and editable text | [Editor](editor-1440.png) | [Editor](editor-390.png) |
| Failed task save, chosen date/text kept | [Error](error-1440.png) | [Error](error-390.png) |
| Saved ordinary task | [Saved](saved-1440.png) | [Saved](saved-390.png) |

Keyboard Enter opened the form, focus moved to the task input, the date began
empty, and saving showed the saved task at both widths. Component tests also
check Skip focus, failed-save text/date preservation, stable retry operation,
locked inputs during a save, and no reminder for failed or uncertain sends.

The reference is Attio's [Introduction to tasks](https://attio.com/help/reference/attio-101/productivity/introduction-to-tasks).
The borrowed parts are an editable task associated with the selected record and
a date/calendar control. Sourcecado adapts this to one optional task after a
confirmed send, shows the saved timezone and concrete date, and requires the
director to choose the date. It does not add automatic natural-language dates,
quick-date presets, recurrence or notifications.

The exact `tasks004.png` linked in #241 was unavailable through the browsing
tool; the current official reference page was checked. These screenshots do
not prove a real Gmail send, the complete native shell, or a pixel comparison
with that unavailable image. Keep those acceptance limits in #212's final
review. The fake-connector tests prove person/message binding and task behavior;
a separately approved real send remains a release acceptance step.
