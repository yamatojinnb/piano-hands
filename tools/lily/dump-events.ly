%% Included before a LilyPond score (lilypond -dinclude-settings=dump-events.ly ...).
%% Writes every musical event of every Voice, one per line, to the file named by the environment variable
%% LILY_EVENTS:  staff  voice  bar  measurePos  measureLength  timeSig  moment  grace  kind  fields...
%% Used by tools/lily2kern.py to turn a LilyPond score into Humdrum **kern for build_song.py.
\version "2.24.0"

#(define dump-port (open-file (or (getenv "LILY_EVENTS") "events.tsv") "w"))
#(define staff-ids (make-hash-table))
#(define voice-ids (make-hash-table))
#(define (ctx-key tab ctx)
   (let ((v (hashq-ref tab ctx #f)))
     (or v (let ((n (hash-count (const #t) tab))) (hashq-set! tab ctx n) n))))
#(define (find-staff ctx)
   (cond ((not ctx) #f)
         ((eq? (ly:context-name ctx) 'Staff) ctx)
         (else (find-staff (ly:context-parent ctx)))))
#(define (mom->str m) (number->string (ly:moment-main m)))
#(define (emit ctx kind . fields)
   (let* ((staff (find-staff ctx))
          (mom (ly:context-current-moment ctx))
          (bar (ly:context-property ctx 'currentBarNumber 0))
          (mpos (ly:context-property ctx 'measurePosition (ly:make-moment 0)))
          (mlen (ly:context-property ctx 'measureLength (ly:make-moment 1)))
          (tsig (ly:context-property ctx 'timeSignatureFraction '(4 . 4))))
     (display (string-join
               (append (list (number->string (if staff (ctx-key staff-ids staff) -1))
                             (number->string (ctx-key voice-ids ctx))
                             (number->string bar) (mom->str mpos) (mom->str mlen)
                             (format #f "~a/~a" (car tsig) (cdr tsig))
                             (mom->str mom) (number->string (ly:moment-grace mom)) kind)
                       (map (lambda (x) (if (string? x) x (format #f "~a" x))) fields))
               "\t") dump-port)
     (newline dump-port)))
#(define (markup->plain m)
   (cond ((string? m) m)
         ((markup? m) (markup->string m))
         (else "")))
#(define (dump-engraver ctx)
   (make-engraver
    (listeners
     ((note-event engraver ev)
      (let* ((p (ly:event-property ev 'pitch))
             (d (ly:event-property ev 'duration))
             (arts (ly:event-property ev 'articulations '()))
             (fing (filter-map (lambda (a) (and (memq 'fingering-event (ly:event-property a 'class))
                                                (ly:event-property a 'digit #f))) arts))
             (tie (any (lambda (a) (memq 'tie-event (ly:event-property a 'class))) arts)))
        (if (ly:pitch? p)
            (emit ctx "note" (ly:pitch-notename p) (ly:pitch-alteration p) (ly:pitch-octave p)
                  (ly:moment-main (ly:duration-length d))
                  (if (null? fing) "" (number->string (car fing)))
                  (if tie "tie" "")))))
     ((rest-event engraver ev)
      (emit ctx "rest" (ly:moment-main (ly:duration-length (ly:event-property ev 'duration)))))
     ((tie-event engraver ev) (emit ctx "tieall"))
     ((fingering-event engraver ev) (emit ctx "fing" (ly:event-property ev 'digit 0)))
     ((absolute-dynamic-event engraver ev) (emit ctx "dyn" (ly:event-property ev 'text "")))
     ((crescendo-event engraver ev) (emit ctx "cresc" (ly:event-property ev 'span-direction 0)))
     ((decrescendo-event engraver ev) (emit ctx "decresc" (ly:event-property ev 'span-direction 0)))
     ((text-script-event engraver ev) (emit ctx "text" (markup->plain (ly:event-property ev 'text ""))))
     ((sustain-event engraver ev) (emit ctx "ped" (ly:event-property ev 'span-direction 0)))
     ((tempo-change-event engraver ev)
      (emit ctx "tempo" (or (ly:event-property ev 'metronome-count #f) "")
            (let ((u (ly:event-property ev 'tempo-unit #f))) (if u (ly:moment-main (ly:duration-length u)) ""))
            (markup->plain (ly:event-property ev 'text "")))))))

\layout {
  \context { \Voice \consists #dump-engraver }
  \context { \Dynamics \consists #dump-engraver }
  \context { \Score \consists #(lambda (ctx) (make-engraver
     (listeners ((tempo-change-event engraver ev)
       (emit ctx "tempo" (or (ly:event-property ev 'metronome-count #f) "")
             (let ((u (ly:event-property ev 'tempo-unit #f))) (if u (ly:moment-main (ly:duration-length u)) ""))
             (markup->plain (ly:event-property ev 'text ""))))))) }
}
