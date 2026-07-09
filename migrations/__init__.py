# Migrations package for Footy Predict.
#
# Each migration is a numbered SQL file in this directory:
#   001_*.sql  — initial schema (applied at DB creation)
#   002_*.sql  — data corrections
#   003_*.sql  — multi-pick predictions constraint
#   ...
#
# Run with:
#   python migrations/run.py --all
#   python migrations/run.py 003
#   python migrations/run.py --status
