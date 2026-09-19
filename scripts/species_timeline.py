"""Plot 15-minute species activity from (POSIX seconds, predictions) entries.

Install: python -m pip install matplotlib
Run:     python species_timeline.py entries.txt --output timeline.png

The input file must contain a Python literal list, like:
[(1787130104, [('LANO', 0.75), ('LACI', 0.15)]), ...]
"""

import argparse
import ast
import colorsys
import math
import textwrap
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


PACIFIC = ZoneInfo("America/Los_Angeles")  # Handles PST/PDT automatically.
WINDOW_SECONDS = 15 * 60
LOCATION = "Jackson Bottom Wetlands Preserve, Hillsboro, OR"
ALTERNATIVE_ALPHA = 0.35
MIN_CONFIDENCE = 0.10
# Latin names supplied by the user.
LATIN_NAMES = {
    "ANPA": "Antrozous pallidus",
    "COTO": "Corynorhinus townsendii",
    "EPFU": "Eptesicus fuscus",
    "EUMA": "Euderma maculatum",
    "LACI": "Lasiurus cinereus",
    "LANO": "Lasionycteris noctivagans",
    "MYCA": "Myotis californicus",
    "MYCI": "Myotis ciliolabrum",
    "MYEV": "Myotis evotis",
    "MYLU": "Myotis lucifugus",
    "MYTH": "Myotis thysanodes",
    "MYVO": "Myotis volans",
    "MYYU": "Myotis yumanensis",
    "PAHE": "Parastrellus hesperus",
    "TABR": "Tadarida brasiliensis",
}


def window_level(confidences):
    """Map a label's predictions in one window to its display level."""
    strongest = max(confidences, default=0.0)
    if strongest < MIN_CONFIDENCE:
        return 0.0
    if strongest > 0.50:
        return 1.0
    if strongest >= 0.25:
        return 0.5
    return 0.1  # At least one retained prediction (confidence >= 10%).


def aggregate_windows(entries, exclude_labels=()):
    """Return labels, windows, counts, means, levels, max count, top-1 flags,
    counts of top-1 recordings with confidence >= 25%, and the sum of numbered
    species bars in each window.

    Predictions below 10% confidence are discarded before aggregation.
    Labels with no retained predictions are omitted. The time axis still
    spans the full recording period, including recordings filtered to empty.

    Each label/window gets a display level based on its strongest prediction:
        100% if any confidence > 50%;
         50% if any confidence >= 25% (and none > 50%);
         10% if any confidence >= 10%; otherwise 0%.

    These are display levels, not aggregate confidence or probabilities.
    Counts, means, and maximum_count are retained as descriptive statistics;
    they do not affect the plotted levels.
    Each input item is counted separately, even if timestamps are identical.
    Histogram totals sum the numbered species bars: only recordings whose
    top-1 label is displayed and has confidence >= 25% contribute. Excluded
    winners (such as NOISE), low-confidence winners, and empty predictions
    do not contribute. Each recording can contribute to at most one label.

    Top-1 means the highest-confidence label in the original recording,
    including excluded labels such as NOISE when determining the winner.
    Exact ties use the first label in the supplied prediction order.
    A window's flag is true if the label wins with confidence >= 10% in ANY
    recording in that window. Annotation counts require confidence >= 25%.
    """
    excluded = set(exclude_labels)
    buckets = defaultdict(list)
    top1_windows = set()
    qualified_top1 = defaultdict(int)
    timestamps = []

    for timestamp, predictions in entries:
        timestamp = float(timestamp)
        if not math.isfinite(timestamp):
            raise ValueError("Timestamps must be finite POSIX seconds.")
        timestamps.append(timestamp)
        # Use elapsed time for binning so repeated DST hours remain distinct.
        # Modern Pacific UTC offsets align with these 15-minute boundaries.
        window = math.floor(timestamp / WINDOW_SECONDS) * WINDOW_SECONDS
        seen = set()
        top_label, top_confidence = None, -1.0
        for label, confidence in predictions:
            if not isinstance(label, str):
                raise ValueError("Labels must be strings.")
            if label in seen:
                raise ValueError(f"Duplicate label in one item: {label!r}")
            seen.add(label)
            confidence = float(confidence)
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError(f"Confidence for {label!r} must be in [0, 1].")
            if confidence > top_confidence:
                top_label, top_confidence = label, confidence
            if label not in excluded and confidence >= MIN_CONFIDENCE:
                buckets[label, window].append(confidence)
        if (top_label is not None and top_label not in excluded
                and top_confidence >= MIN_CONFIDENCE):
            top1_windows.add((top_label, window))
            if top_confidence >= 0.25:
                qualified_top1[top_label, window] += 1

    if not timestamps:
        raise ValueError("No recordings to plot.")

    labels = sorted({label for label, _ in buckets})
    first = math.floor(min(timestamps) / WINDOW_SECONDS) * WINDOW_SECONDS
    last = math.floor(max(timestamps) / WINDOW_SECONDS) * WINDOW_SECONDS
    windows = list(range(first, last + WINDOW_SECONDS, WINDOW_SECONDS))
    maximum_count = max(map(len, buckets.values()), default=0)
    counts, means, weights, top1, top1_counts = {}, {}, {}, {}, {}
    for label in labels:
        values = [buckets.get((label, window), ()) for window in windows]
        counts[label] = [len(v) for v in values]
        means[label] = [math.fsum(v) / len(v) if v else 0.0 for v in values]
        weights[label] = [window_level(v) for v in values]
        top1[label] = [(label, window) in top1_windows for window in windows]
        top1_counts[label] = [qualified_top1[label, window] for window in windows]

    # Match the exact annotation rule used for the species bars below.
    total_recordings = [
        sum(top1_counts[label][i] for label in labels
            if weights[label][i] >= 0.5 and top1[label][i])
        for i in range(len(windows))
    ]

    return (labels, windows, counts, means, weights, maximum_count, top1,
            top1_counts, total_recordings)


def plot_species_timeline(entries, exclude_labels=("NOISE",), location=LOCATION):
    """Plot the sum of numbered species bars first, then species rows.

    Returns (figure, axes), with the total-recordings histogram at axes[0].
    Histogram counts are scaled to 0..1, with the busiest window at 1.
    Species rows retain their 0..1 display scale. All rows share Pacific time.
    """
    (labels, windows, counts, means, weights, maximum_count, top1,
     top1_counts, total_recordings) = aggregate_windows(entries, exclude_labels)
    x = mdates.date2num(
        [datetime.fromtimestamp(t, timezone.utc) for t in windows]
    )
    width = WINDOW_SECONDS / 86400  # Matplotlib date units are days.
    # Exactly equal hue spacing; sorting makes assignments repeatable for
    # the same set of labels. Adding/removing labels redistributes the hues.
    colors = {
        label: colorsys.hsv_to_rgb(i / len(labels), 0.75, 0.78)
        for i, label in enumerate(labels)
    }

    figure_height = max(4, 3.40 + 0.72 * len(labels))
    fig, axes = plt.subplots(
        len(labels) + 1, 1, sharex=True, sharey=False, squeeze=False,
        gridspec_kw={"height_ratios": [1.65] + [1] * len(labels)},
        figsize=(14, figure_height),
    )
    axes = axes[:, 0]
    total_ax = axes[0]
    peak_count = max(total_recordings)
    normalized_recordings = [
        count / peak_count if peak_count else 0.0 for count in total_recordings
    ]
    total_ax.bar(x, normalized_recordings, width=width, align="edge",
                 color="#888888", edgecolor="white", linewidth=0.3)
    total_ax.set_ylim(0, 1)
    total_ax.set_yticks([0, 0.5, 1])
    total_ax.tick_params(axis="y", length=0, labelleft=False)
    total_ax.set_ylabel("Detections",
                        rotation=0, ha="right", va="center",
                        labelpad=16, color="#555555", fontweight="bold")

    for ax, label in zip(axes[1:], labels):
        luminance = sum(
            weight * (channel / 12.92 if channel <= 0.04045
                      else ((channel + 0.055) / 1.055) ** 2.4)
            for weight, channel in zip((0.2126, 0.7152, 0.0722), colors[label])
        )
        number_color = "black" if luminance > 0.179 else "white"
        bar_colors = [
            (*colors[label], 1.0 if was_top1 else ALTERNATIVE_ALPHA)
            for was_top1 in top1[label]
        ]
        ax.bar(x, weights[label], width=width, align="edge",
               color=bar_colors, edgecolor="white", linewidth=0.3)
        for left, level, was_top1, count in zip(
            x, weights[label], top1[label], top1_counts[label]
        ):
            if level >= 0.5 and was_top1:
                # Center horizontally and keep a fixed inset above the baseline.
                ax.text(left + width / 2, 0.06, str(count),
                        ha="center", va="bottom", color=number_color,
                        fontsize=9, fontweight="bold", clip_on=True)
        ax.set_ylim(0, 1)
        ax.set_yticks([])
        ax.tick_params(axis="y", labelsize=7, length=0, colors="#777777")
        ax.set_ylabel(label, rotation=0, ha="right", va="bottom",
                      labelpad=16, color=colors[label], fontweight="bold")
        ax.yaxis.set_label_coords(-0.02, 0.60)
        latin_name = LATIN_NAMES.get(label)
        if latin_name:
            ax.text(-0.02, 0.48,
                    textwrap.fill(latin_name, width=22, break_long_words=False,
                                  break_on_hyphens=False),
                    transform=ax.transAxes, ha="right", va="top",
                    fontsize=8, color="#777777", fontstyle="italic",
                    linespacing=1.1, clip_on=False)

    midnight_positions = []
    for timestamp in windows + [windows[-1] + WINDOW_SECONDS]:
        local_time = datetime.fromtimestamp(timestamp, PACIFIC)
        if local_time.hour == 0 and local_time.minute == 0:
            midnight_positions.append(mdates.date2num(local_time))

    for ax in axes:
        ax.set_facecolor("#f7f7f7")
        ax.set_axisbelow(True)
        ax.grid(axis="y", which="both", visible=False)
        ax.grid(axis="x", which="major", color="#888888", linewidth=1.2, alpha=0.5)
        ax.grid(axis="x", which="minor", color="#d9d9d9", linewidth=0.5,
                linestyle="--")
        for midnight in midnight_positions:
            ax.axvline(midnight, color="#d32f2f", linewidth=1.6, zorder=4)
        ax.tick_params(axis="x", which="both", length=0, labelsize=9)
        for spine in ax.spines.values():
            spine.set_visible(False)

    axes[-1].set_xlim(x[0], x[-1] + width)
    axes[-1].xaxis.set_major_locator(mdates.HourLocator(tz=PACIFIC))
    axes[-1].xaxis.set_major_formatter(
        mdates.DateFormatter("%b %d\n%H:%M %Z", tz=PACIFIC)
    )
    axes[-1].xaxis.set_minor_locator(
        mdates.MinuteLocator(byminute=[0, 15, 30, 45], tz=PACIFIC)
    )
    axes[-1].set_xlabel("15-minute Intervals", labelpad=10)
    start = datetime.fromtimestamp(windows[0], PACIFIC)
    end = datetime.fromtimestamp(windows[-1] + WINDOW_SECONDS, PACIFIC)
    # Use fixed physical header spacing so filtering out rows cannot cause
    # the title, explanatory text, and legend to overlap.
    def header_y(inches_from_top):
        return 1 - inches_from_top / figure_height

    fig.suptitle("EPRI Prototype Test Field Deployment", fontsize=17, y=header_y(0.12))
    fig.text(0.5, header_y(0.42), location, ha="center", va="top",
             fontsize=12, fontweight="bold", color="#333333")
    fig.text(
        0.5, header_y(0.69),
        f"{start:%b %d, %Y %H:%M %Z} – {end:%b %d, %Y %H:%M %Z}\n"
        "Full bar if any detection above 50%; Half bar if any detection "
        "above 25%; Low bar if any confidence above 10%",
        ha="center", va="top", fontsize=9, color="#555555",
    )
    fig.legend(
        handles=[
            Patch(facecolor="#555555", label="Species in Top-1 (Highest Confidence)"),
            Patch(facecolor="#555555", alpha=ALTERNATIVE_ALPHA,
                  label=f"Species in Top-5"),
        ],
        loc="upper center", bbox_to_anchor=(0.5, header_y(1.03)),
        ncol=2, frameon=False, fontsize=9,
    )
    # fig.text(0.5, header_y(1.31),
    #          "Grey bars = sum of numbered species bars, normalized to the peak; "
    #          "species-bar numbers = top-1 files with confidence ≥25%",
    #          ha="center", va="top", fontsize=9, color="#555555")
    fig.subplots_adjust(left=0.165, right=0.985, bottom=0.75 / figure_height,
                        top=header_y(1.65), hspace=0.22)
    return fig, axes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Text file containing the entries")
    parser.add_argument("--output", type=Path, help="Save the chart, e.g. timeline.png")
    parser.add_argument("--exclude", nargs="*", default=["NOISE"],
                        help="Labels to omit (default: NOISE)")
    args = parser.parse_args()
    entries = ast.literal_eval(args.input.read_text(encoding="utf-8"))
    fig, _ = plot_species_timeline(entries, exclude_labels=args.exclude)
    if args.output:
        fig.savefig(args.output, dpi=180, facecolor="white")
        plt.close(fig)
    else:
        plt.show()


if __name__ == "__main__":
    main()
