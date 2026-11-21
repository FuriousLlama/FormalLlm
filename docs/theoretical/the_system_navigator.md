# The System Navigator: From Functions to Systems

*Written 2026-02-23. Extends `the_machine.md` from individual function synthesis to system-level operation.*

---

## Beyond Individual Functions

The machine described in `the_machine.md` synthesizes individual functions: give it a type signature, get back an implementation. But nobody wakes up in the morning needing a `map` function. They need a system that processes user uploads, or a compiler that targets ARM, or a trading engine that handles margin calls.

The gap between "synthesize one function" and "build a system" is where the real architecture lives.

---

## What Drives the Machine

The machine is not operated by a human typing type signatures into a terminal. That is the sprint-level testing interface, not the real one.

The real driver is a **decomposition cascade**. Something — a human, another system, or the machine itself — starts with a high-level goal and recursively breaks it into sub-goals until each sub-goal is a type signature the machine can inhabit.

---

## The Interaction Layers

### Layer 1: Intent (Human-Facing)

A human expresses what they want. This is inherently informal — natural language, sketches, examples, existing code they want to modify. "I need a web server that rate-limits by API key."

This layer is a *translation* problem: map informal intent to formal specification. This is where an LLM is genuinely useful — not for writing code, but for translating human intent into type signatures, properties, and decomposition plans. The LLM speaks human. The machine speaks types. The LLM is the translator, not the programmer.

### Layer 2: Architecture (Decomposition)

The translated intent becomes a **module signature** — a set of typed interfaces that together constitute the system. A rate-limiting web server decomposes into:

```haskell
type RateLimiter = APIKey -> Timestamp -> IO (Either RateLimitError ())
type Handler     = Request -> IO Response
type Middleware  = Handler -> Handler
type Config      = { maxRequests :: Int, windowSeconds :: Int }

rateLimiter  :: Config -> IO RateLimiter
withRateLimit :: RateLimiter -> Middleware
router        :: [(Method, Path, Handler)] -> Handler
serve         :: Port -> Handler -> IO ()
```

Each of these is a typed hole. The decomposition engine produces this module skeleton. The types *are* the architecture. The composition rules *are* the API contracts. Nothing is informal at this level.

**Evolutionary search for decomposition.** The set of constituents for the architecture is bounded by external constraints — requirements, deployment environment, existing systems, organizational structure — that the navigator has no way of assessing from within formal space. Evolutionary algorithms are a natural fit: generate many possible module decompositions, evaluate fitness (does the composed system compile, pass tests, meet constraints), evolve better architectures. The navigator serves as the fitness evaluator's engine — it fills in each candidate architecture to test whether it works.

### Layer 3: Synthesis (Per-Typed-Hole)

The machine operates here. Each typed hole in the module skeleton is a synthesis task. `rateLimiter :: Config -> IO RateLimiter` gets dispatched to the machine with:

- The type signature (the coordinate)
- The library context (what is in scope: `IORef`, `Map`, `getCurrentTime`, etc.)
- Properties if available (`rateLimiter config` should reject the (N+1)th request within the window)

The machine fills each hole independently. The type system guarantees that the filled holes compose correctly — if `withRateLimit` has type `RateLimiter -> Middleware` and `rateLimiter` has type `Config -> IO RateLimiter`, composition is guaranteed to type-check. No integration testing needed at the type level.

### Layer 4: Verification (Per-System)

Individual functions pass their local tests, but the composed system needs integration verification. Does the rate limiter actually work under concurrent load? Does the router dispatch correctly? This is the dynamic verification layer operating at the system level — property-based tests, integration tests, load tests.

Failures here feed back to Layer 2 (wrong decomposition) or Layer 3 (wrong implementation).

---

## The Unit of Operation

The machine does not operate per-function. It does not operate per-file. It operates at **the level of the typed hole**.

A typed hole can be:

- A single function (`map :: (a -> b) -> [a] -> [b]`)
- A module interface (a set of mutually-dependent typed functions)
- A typeclass instance (a coherent set of operations satisfying laws)
- A transformation between two module signatures (a refactoring)

The granularity is determined by the type system, not by file structure. A typed hole is the natural unit because it is the largest thing the type checker can verify independently. Below this level, the machine has full autonomy. Above this level, something else (the decomposition engine, the human, the LLM) makes the architectural decisions.

The answer to "per function or per system" is: **per system, but the system is decomposed into typed holes and each hole is filled independently**. The type system is the glue that guarantees the independent pieces compose.

---

## What the Human Sees

The human does not see type signatures. They see something closer to this:

1. They describe what they want (natural language, examples, sketches)
2. The system proposes an architecture (a module skeleton with typed interfaces)
3. The human reviews and adjusts the architecture ("no, the rate limiter should be per-endpoint, not per-key")
4. The system fills in the implementation
5. The human reviews the result, running and modifying it

The interaction is at the **architectural level**, not the implementation level. The human makes decisions about *what the system should do* and *how it should be structured*. The machine handles *how each piece is implemented*.

This is the natural division of labor: humans are good at intent and structure, machines are good at search within formal constraints. The human never writes a function body. They never debug a type error. They never track down a null pointer. Those are all problems that exist *within* the formal space where the machine operates. The human operates *above* the formal space, in the domain of intent and architecture.

---

## Three Stages of the Future

### Near Term (Sprints 5-8)

The machine is a *function synthesizer*. You give it a type, it gives you an implementation. It is a tool — like a calculator, but for typed programs. A developer uses it to fill in tedious implementations: "here's the type, generate the body." It saves time but does not change how software is built. This is the "better autocomplete" stage, except grounded in types rather than statistical text completion.

### Medium Term (Phases 3-5 Matured)

The machine is a *module synthesizer*. It takes a module signature — a set of typed interfaces — and fills in all the implementations, verified against properties. The human works at the architectural level: designing module boundaries, specifying properties, reviewing generated implementations. Software development becomes *architecture and review*, not *implementation and debugging*.

This is where the library (Phase 6) becomes transformative. Every project the machine completes adds verified building blocks. Over time, most typed holes match existing library entries. Synthesis becomes mostly lookup, with genuine search only at the edges — novel compositions or new domains. The machine gets faster as it works more, not because the model improves but because the library grows.

### Long Term (The Full Navigator)

The machine is a *system synthesizer*. It takes a high-level specification (types + properties + constraints) and produces a complete, verified system. The decomposition engine operates recursively — breaking systems into modules, modules into functions, functions into compositions of library primitives. The human specifies *what* at the highest level and reviews *what* the machine produced. The *how* — at every level of abstraction — is the machine's domain.

At this stage, the machine is not writing code in the way we think of it. It is **navigating the space of all typed programs that satisfy a specification**, using search guided by a learned prior, constrained by a type checker, verified by execution, and accelerated by a library of previously verified components. Code is the output, not the medium. The medium is types.

---

## What Remains Uncertain

The decomposition layer (Layer 2) is the hardest part. Breaking "rate-limiting web server" into a set of typed interfaces requires architectural judgment — understanding what abstractions exist, which decompositions lead to maintainable systems, how to balance generality against simplicity. This is the part of software engineering that experienced developers do well and that no current system handles reliably.

It is possible that the LLM layer is sufficient for architectural decomposition — LLMs are surprisingly good at architectural reasoning when the domain is well-understood. It is also possible that evolutionary search over decompositions, with the navigator as the fitness evaluator, is the right approach. It is also possible that this is where the machine fundamentally needs human guidance, even in the long term. Architectural decisions are often value judgments (simplicity vs. flexibility, performance vs. readability) that do not have formal right answers.

The honest answer: the machine operates *below* the decomposition layer with high confidence, and *at* the decomposition layer with uncertainty. Sprint 5 is testing the bottom. The top is a harder problem that may or may not yield to the same approach.

---

*This document extends `the_machine.md` from per-function synthesis to system-level operation, describing the interaction layers, the unit of operation, and the projected evolution from function synthesizer to system synthesizer.*
