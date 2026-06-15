# Single Model Gateway

Sourcecado will route model calls through one internal Model Gateway instead of scattering direct provider calls across memory, chat, routines, tools, and validation. The gateway records named model tasks, prompt/version names, usage, errors, structured-output parsing, and Run Ledger links so the system can explain and debug model behavior consistently.
