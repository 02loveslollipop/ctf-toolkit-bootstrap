# OpenCROW Crypto Toolbox

Use this reference when choosing between the Python-first crypto tools and cracking helpers mapped into the `ctf` environment.

## Python modules

- `z3`: SAT/SMT solving, bit-vectors, arithmetic constraints, state recovery, and symbolic key search.
- `fpylll`: LLL and BKZ lattice reduction for Hidden Number, approximate common divisor, knapsack, and Coppersmith-adjacent workflows when Sage is unnecessary.
- `pycryptodome`: symmetric and asymmetric primitives, hashes, MACs, padding, and protocol implementation glue.

## Native tools

- `hashcat`: high-throughput hash cracking and rule/mask attacks.
- `john`: broad format support and quick local cracking workflows.
- `factordb_lookup.py`: ask FactorDB whether a large integer is already factored publicly.

## Practical selection

- Start with `z3` when the unknowns are discrete variables and the relationships are exact.
- Start with `fpylll` when the attack is "build a basis, reduce it, and inspect short vectors."
- Use `pycryptodome` when the cryptographic primitive itself is the bottleneck, not the math around it.
- Use `hashcat` or `john` when the problem is fundamentally password or hash recovery.
- Try FactorDB before spending time on local factoring of suspiciously challenge-sized RSA moduli.
- Use plain Python around both libraries for parsing packets, ciphertext blobs, or challenge-specific encodings.
- Switch to `sagemath` when the task needs finite fields, elliptic curves, polynomial rings, resultants, or Sage-native lattice helpers.
