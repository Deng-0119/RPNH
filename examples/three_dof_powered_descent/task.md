# 3-DOF powered-descent task

Design and execute a multi-agent workflow that produces a feasible numerical
solution for the powered-descent problem in the attached `problem.json`.
Choose the workflow topology and numerical method yourself. Do not assume a
predefined research workflow.

The final registered deliverable must include:

1. a self-contained implementation that generates the trajectory;
2. a machine-readable trajectory with state, thrust and integration-step data;
3. an independent numerical validation of every stated terminal, dynamics and
   path constraint; and
4. a concise engineering report that distinguishes observed feasibility from
   any broader optimality or robustness claim.

The terminal position and velocity limits are acceptance criteria, not soft
objectives. Execute the generated implementation in the workspace and report
observed failures honestly. A solver exit status alone is not acceptance.

Name the machine-readable trajectory `descent_solution.json`. Its `samples`
array must use rows of
`[dt, r0, r1, r2, v0, v1, v2, mass, T0, T1, T2]`; every propagation row has
`0 < dt <= 0.01`, and one final state row has `dt = 0`. This lets the supplied
independent verifier check the result without depending on the chosen solver.
