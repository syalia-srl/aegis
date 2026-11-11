- **Model scores refresh every night, without a release.** A workflow rebuilds the Artificial Analysis ranking nightly and publishes it on the `model-ranking` branch; every aegis downloads it at start and then daily, reads it while it is newer than the packaged one, and keeps the last good copy when a download fails. `model_ranking: false` turns the download off.

help: config#models
