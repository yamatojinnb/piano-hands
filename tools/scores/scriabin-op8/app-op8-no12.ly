%% For the practice app (tools/lily2kern.py): the same music as etude-op8-no12-individual.ly,
%% laid out as one score that also carries the pedal marks (they are only in the MIDI score of the original).
\version "2.24.0"
\include "includes/header-paper.ily"
\include "includes/etude-op8-no12-parts.ily"

\score {
  \keepWithTag layout
  \new PianoStaff <<
    \new Staff = "upper" \rightHand
    \new Dynamics \dynamics
    \new Staff = "lower" \leftHand
    \new Dynamics \pedal
    \new Dynamics \tempi
  >>
  \layout {}
}
