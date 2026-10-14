<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Writing for the box

These rules apply to every word a person reads: page text, labels and buttons, messages from the hub and the root helper, installer output, manifests and the user guide. They do not apply to code comments, log lines or commands.

## The rules

1. **Write to the reader as "you".** Call the device "the box". Use "administrator" only for someone other than the reader, for example on a page that guests read.
2. **One idea per sentence.** Aim for 15 words. Stop at 25.
3. **Start an instruction with its verb.** Put any condition first: "If the hotspot is off, select Start."
4. **Use the words on the screen, and one name for each thing.** Quote a control by its exact label. The [glossary](../user/glossary.md) decides the name of a thing.
5. **When something goes wrong, say what happened, then what to do.** Do not write "invalid", "error" or "failed" on their own.
6. **Use plain words.** can, use, select, need, sign in. Avoid may, ensure, utilise, and negative contractions such as "can't" and "don't".
7. **Sentence case everywhere.** No semicolons. No "e.g." or "i.e.". Explain an abbreviation the first time it appears.

## Messages when something goes wrong

| Not this | This |
|---|---|
| Invalid username | Enter a username of 3 to 32 letters or numbers |
| Sign-in failed | The username or password is not correct |
| Request failed. Select the button to try again. | The box could not turn on internet access. Try again. |
| Verification failed | The box did not install the update. Its files did not pass the check. |

A short heading for success is fine: "Report sent", "Saved".

## Words

| Use | Not |
|---|---|
| Sign in, Sign out, Create an account | Log in, login, register |
| administrator | admin, owner |
| Select | Click, choose |
| can | may |
| Download | Fetch |
| USB drive | stick |

Labels that name the same option keep the same words everywhere: on its page, in the theme menu, in messages and in the guide. `tests/guide_labels_guard.py` checks that every label the guide quotes still exists.

## Writing a message in code

Write each case as a whole sentence and select it in code. Do not build a sentence from parts joined with `+`, and do not compute plurals inline:

```python
# Not this
f"{n} link{'s' if n != 1 else ''} {'is' if n == 1 else 'are'} outside its folder"
# This
"1 link is outside its folder." if n == 1 else f"{n} links are outside their folders."
```

Keep commands, paths and log lines out of sentences: show them on their own, so they can be copied.
