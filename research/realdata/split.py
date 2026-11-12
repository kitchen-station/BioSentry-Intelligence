"""The patient-level split, fixed before any analysis.

Test = the 46 hospitals the generator fit (fit-generator.mjs) did not use; 38 of them were used by the circadian calibration fit (fit-eicu.mjs), so this is mostly, not fully, a different-site test.
(fit-eicu.mjs uses its own 1-in-5 hospital hash split; checked: 38 of these 46 ids are on its fit side.)
Originally described as: the hospitals the simulator's vitals generator never saw. Source: the sim repo's
`scripts/fit-generator.mjs` line 71: hospital ids sorted numerically, `i % 4 === 3` held out (46 of
186 ids in the eICU-CRD Demo `patient` table; its episodes skip them at line 295). Encoded here as ids
so the split cannot drift; the loader recomputes the rule from the data and refuses on a difference.
Everything else is fit data (train and validation, for a later slice) and is never loaded by the
evaluation command.
"""

TEST_HOSPITALS = frozenset({
    60, 67, 73, 93, 112, 123, 138, 143, 152, 158, 171, 179, 183, 195, 199, 203, 207, 215, 226, 245,
    250, 254, 262, 267, 272, 279, 283, 307, 328, 338, 352, 357, 364, 383, 388, 392, 397, 402, 407,
    413, 420, 424, 433, 437, 443, 452,
})


def held_out_by_rule(hospital_ids: set[int]) -> frozenset[int]:
    return frozenset(h for i, h in enumerate(sorted(hospital_ids)) if i % 4 == 3)
