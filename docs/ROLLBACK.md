# Rollback

1. Stop accepting new Gateway turns and let active turns finish.
2. Remove current hooks: `python -m hermes_lark_streaming uninstall`.
3. Install the previous wheel from the GitHub release assets.
4. Run `python -m hermes_lark_streaming verify` and then `install`.
5. Restart Gateway once and run `doctor` plus the offline `smoke` command.

If installation was interrupted, use `python -m hermes_lark_streaming restore`; managed backup
files are validated by the patcher before publication. Configuration is backward compatible:
unknown `streaming` fields are ignored by older versions. Keep a copy of `config.yaml` and the
Git SHA being rolled back to.

