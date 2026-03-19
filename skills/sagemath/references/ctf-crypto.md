# CTF Crypto Patterns

Load this reference when the task is cryptanalytic and SageMath is likely to save time over plain Python.

## RSA and Modular Arithmetic

Use Sage for:

- factoring composites when small structure is suspected
- modular inverses, CRT recombination, and solving congruences
- polynomial modeling for partial key exposure or related-message attacks
- checking conditions for Wiener's, Boneh-Durfee, and small-root style approaches
- testing toy `small_roots()` constructions before adapting them to the target

Starter pattern:

```python
n = 101 * 113
e = 17
phi = euler_phi(n)
d = inverse_mod(e, phi)
print(d)
```

## Finite Fields and Elliptic Curves

Use Sage for:

- creating `GF(p)` and extension fields
- manipulating curve points and orders
- solving discrete-log style toy instances
- checking invalid-curve or subgroup structure ideas

Starter pattern:

```python
F = GF(10177)
E = EllipticCurve(F, [2, 3])
P = E(3, 6)
print(P.order())
```

## Lattices and Hidden Structure

Use Sage for:

- building integer lattices with `Matrix(ZZ, ...)`
- running `LLL()` for small-solution recovery
- experimenting with hidden number and approximate relation attacks

Starter pattern:

```python
M = Matrix(ZZ, [[q, 0], [a, 1]])
print(M.LLL())
```

## PRNG Cryptanalysis

Use Sage for:

- solving linear recurrences
- recovering parameters of LCG-like generators from outputs
- modeling bit relations or modular equations from partial output leaks
- building transition matrices for state stepping
- reversing MT19937 tempering and checking candidate state words
- tracking partially known or masked MT19937 state bits
- expressing xorshift families as linear maps over `GF(2)`
- reconstructing full MT19937 state from 624 observed outputs
- brute-forcing or constraining truncated LCG low bits before algebraic lifting

Starter pattern:

```python
M = Matrix(ZZ, [[1103515245, 12345], [0, 1]])
print(M^5)
```

## Practical Guidance

- Keep scripts in `.sage` files when the attack is more than a few lines.
- Print intermediate values that matter for debugging: moduli, polynomial degree, lattice dimensions, recovered candidates.
- Prefer deterministic algebra first; only brute force after reducing the search space.
- If Sage syntax is uncertain, write a small probe snippet and run it through the bundled runner before building the full exploit.
- Start from the templates in `~/.codex/skills/sagemath/assets/templates/` instead of rebuilding common attack scaffolds from scratch.
- Use the nonce-reuse and hidden-number lattice templates as starting points for ECDSA-style signature attacks.
- Treat Boneh-Durfee and partial-nonce templates as scaffolds: tune dimensions and bounds to the exact challenge instead of expecting one-size-fits-all parameters.
