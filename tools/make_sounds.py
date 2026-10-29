#!/usr/bin/env python3
"""Builds frontend/sounds.txt, the sound-it-out spellings Language mode shows
under each caption word ("broccoli" -> "BRAH-kuh-lee"), from the CMU
Pronouncing Dictionary (tools/cmudict.dict). American English.

One line per word: "word<TAB>spelling". Syllables are joined by "-"; in a
word of two or more syllables the stressed one is in capitals.

    python3 tools/make_sounds.py
"""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(HERE, "..", "frontend")

CONSONANTS = {
    "B": "b", "CH": "ch", "D": "d", "DH": "th", "F": "f", "G": "g", "HH": "h", "JH": "j",
    "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ng", "P": "p", "R": "r", "S": "s",
    "SH": "sh", "T": "t", "TH": "th", "V": "v", "W": "w", "Y": "y", "Z": "z", "ZH": "zh",
}
# Consonant groups a syllable can start with; between two vowels the next
# syllable takes the longest of these (so "computer" is kuhm-PYOO-tur).
ONSETS = {tuple(o.split()) for o in [
    "P R", "P L", "P Y", "B R", "B L", "B Y", "T R", "T W", "D R", "D W", "K R", "K L", "K W", "K Y",
    "G R", "G L", "G W", "F R", "F L", "F Y", "TH R", "SH R", "S P", "S T", "S K", "S M", "S N",
    "S L", "S W", "S P L", "S P R", "S T R", "S K R", "S K W", "S K Y", "M Y", "N Y", "HH Y", "V Y",
]}
# Short vowels: a stressed one can't end a syllable in English, so it keeps
# one consonant ("every" is EV-ur-ee, not EH-vur-ee).
CHECKED = {"AE", "EH", "IH", "UH", "AH"}


def is_vowel(phone):
    return phone[-1].isdigit()


def syllables(phones):
    """[(onset, vowel, coda)] with phones split by the longest-onset rule."""
    vowels = [i for i, p in enumerate(phones) if is_vowel(p)]
    out = []
    start = 0
    for n, v in enumerate(vowels):
        if n + 1 == len(vowels):
            out.append((phones[start:v], phones[v], phones[v + 1:]))
            break
        middle = phones[v + 1:vowels[n + 1]]
        split = len(middle)  # how many consonants stay in this syllable's coda
        for k in range(len(middle) + 1):
            rest = tuple(middle[k:])
            if len(rest) <= 1 or rest in ONSETS:
                split = k
                break
        if middle[split:] and middle[split] == "NG":
            split += 1  # "ng" never starts a syllable
        if split == 0 and middle and phones[v][-1] == "1" and phones[v][:-1] in CHECKED:
            split = 1
        out.append((phones[start:v], phones[v], middle[:split]))
        start = v + 1 + split
    return out


def vowel_spelling(vowel, onset, coda):
    base, stress = vowel[:-1], vowel[-1]
    closed = bool(coda)
    r = coda[:1] == ["R"]
    if base == "AH":
        return "u" if stress != "0" and closed else "uh"
    if base == "ER":
        return "ur"
    if base == "AA":
        return "a" if r else ("o" if closed else "ah")
    if base == "AO":
        return "o" if r else "aw"
    if base == "EH":
        return "ai" if r else ("e" if closed else "eh")
    if base == "IH":
        return "i" if closed else "ih"
    if base == "AY":
        return "eye" if not onset else "y"
    return {"AE": "a", "EY": "ay", "IY": "ee", "OW": "oh", "OY": "oy", "AW": "ow",
            "UH": "uu", "UW": "oo"}[base]


def respell(phones):
    parts = syllables(phones)
    out = []
    for onset, vowel, coda in parts:
        text = "".join(CONSONANTS[p] for p in onset) + vowel_spelling(vowel, onset, coda)
        text += "".join(CONSONANTS[p] for p in coda)
        if vowel[:-1] == "AY" and onset and len(coda) == 1:
            text += "e"  # "time" -> tyme, so the y reads as in "my"
        if len(parts) > 1 and vowel[-1] == "1":
            text = text.upper()
        out.append(text)
    return "-".join(out)


def main():
    rows = {}
    with open(os.path.join(HERE, "cmudict.dict"), encoding="utf-8") as f:
        for line in f:
            word, *phones = line.split("#")[0].split()
            # ponytail: first pronunciation only, so "read" is always "reed";
            # picking by context would need part-of-speech tagging
            if "(" in word or word in rows or not word.replace("'", "").isalpha():
                continue
            rows[word] = respell(phones)
    with open(os.path.join(FRONTEND, "sounds.txt"), "w", encoding="utf-8") as f:
        f.write("# Sound-it-out spellings for Language mode, made by tools/make_sounds.py\n")
        f.write("# from the CMU Pronouncing Dictionary; see sounds-LICENSE.txt for its notice.\n")
        f.writelines(f"{w}\t{s}\n" for w, s in sorted(rows.items()))
    shutil.copy(os.path.join(HERE, "cmudict-LICENSE.txt"), os.path.join(FRONTEND, "sounds-LICENSE.txt"))
    print(f"{len(rows)} words")


for phones, expected in [
    ("B R AA1 K AH0 L IY0", "BRAH-kuh-lee"),
    ("K AH0 M P Y UW1 T ER0", "kuhm-PYOO-tur"),
    ("EH1 V ER0 IY0", "EV-ur-ee"),
    ("IH1 NG G L IH0 SH", "ING-glish"),
    ("T IY1 CH ER0", "TEE-chur"),
    ("K AE1 T", "kat"),
    ("T AY1 M", "tyme"),
    ("K AA1 R", "kar"),
    ("AH0 N T IH1 L", "uhn-TIL"),
]:
    assert respell(phones.split()) == expected, (phones, respell(phones.split()))

if __name__ == "__main__":
    main()
