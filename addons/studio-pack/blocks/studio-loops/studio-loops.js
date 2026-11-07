/* Studio Pack: loop and ping-pong motion for HyperFrames timelines (Resolve 21's loop / ping-pong keyframe modes).
 *
 * HyperFrames drives each timeline's time itself, so `repeat: -1` would make the timeline endless. This adds a
 * finite number of repeats that fills exactly the span asked for.
 *
 *   StudioLoops.add(tl, target, fromVars, toVars, {
 *     start: 1,          // seconds into the timeline
 *     end: 9,            // stop by here (a cycle never runs past it)
 *     cycle: 1.2,        // seconds per pass
 *     mode: "loop",      // "loop" (jump back and replay) | "pingpong" (there and back) | "relative" (keep adding)
 *     ease: "sine.inOut"
 *   });
 *
 * "relative" adds the same change every cycle, e.g. { rotation: "+=90" } steps round by 90 degrees each time.
 */
(function (global) {
  function add(tl, target, fromVars, toVars, opts) {
    opts = opts || {};
    var start = Number(opts.start) || 0;
    var cycle = Number(opts.cycle) || 1;
    var end = opts.end != null ? Number(opts.end) : start + cycle;
    var mode = opts.mode || "loop";
    var passes = Math.max(1, Math.floor((end - start) / cycle + 1e-6));
    var ease = opts.ease || (mode === "pingpong" ? "sine.inOut" : "none");
    if (mode === "relative") {
      for (var i = 0; i < passes; i++) {
        tl.to(
          target,
          Object.assign({}, toVars, { duration: cycle, ease: ease }),
          start + i * cycle,
        );
      }
      return tl;
    }
    var vars = Object.assign({}, toVars, {
      duration: cycle,
      ease: ease,
      repeat: passes - 1,
      yoyo: mode === "pingpong",
      immediateRender: false,
    });
    tl.fromTo(target, fromVars, vars, start);
    return tl;
  }
  global.StudioLoops = { add: add };
})(window);
