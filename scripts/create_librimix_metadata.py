import argparse
import os
import random
import warnings
from math import ceil

import numpy as np
import pandas as pd
import pyloudnorm as pyln
import soundfile as sf
from tqdm import tqdm

# Some parameters
# eps secures log and division
EPS = 1e-10
# max amplitude in sources and mixtures
MAX_AMP = 0.9
# We will filter out files shorter than that
NUMBER_OF_SECONDS = 3
# In LibriSpeech all the sources are at 16K Hz
RATE = 16000
# We will randomize loudness between this range
MIN_LOUDNESS = -33
MAX_LOUDNESS = -25

# A random seed is used for reproducibility
random.seed(123)

# Command line arguments
parser = argparse.ArgumentParser()
parser.add_argument('--librispeech_dir', type=str, required=True,
                    help='Path to librispeech root directory')
parser.add_argument('--librispeech_md_dir', type=str, required=True,
                    help='Path to librispeech metadata directory')
parser.add_argument('--wham_dir', type=str, required=True,
                    help='Path to wham root directory')
parser.add_argument('--wham_md_dir', type=str, required=True,
                    help='Path to wham metadata directory')
parser.add_argument('--metadata_outdir', type=str, default=None,
                    help='Where librimix metadata files will be stored.')
parser.add_argument('--n_src', type=int, required=True,
                    help='Number of sources desired to create the mixture')


def main(args):
    librispeech_dir = args.librispeech_dir
    librispeech_md_dir = args.librispeech_md_dir
    wham_dir = args.wham_dir
    wham_md_dir = args.wham_md_dir
    n_src = args.n_src
    # Librimix metadata directory
    md_dir = args.metadata_outdir
    if md_dir is None:
        root = os.path.dirname(librispeech_dir)
        md_dir = os.path.join(root, f'LibriMix/metadata')
    os.makedirs(md_dir, exist_ok=True)
    create_librimix_metadata(librispeech_dir, librispeech_md_dir, wham_dir,
                             wham_md_dir, md_dir, n_src)


def create_librimix_metadata(librispeech_dir, librispeech_md_dir, wham_dir,
                             wham_md_dir, md_dir, n_src):
    """ Generate LibriMix metadata according to LibriSpeech metadata """

    # Dataset name
    dataset = f'libri{n_src}mix'
    # List metadata files in LibriSpeech
    librispeech_md_files = os.listdir(librispeech_md_dir)
    # List metadata files in wham_noise
    wham_md_files = os.listdir(wham_md_dir)
    # If you wish to ignore some metadata files add their name here
    # Example : to_be_ignored = ['dev-other.csv']
    to_be_ignored = []

    check_already_generated(md_dir, dataset, to_be_ignored,
                            librispeech_md_files)
    # Go through each metadata file and create metadata accordingly
    for librispeech_md_file in librispeech_md_files:
        if not librispeech_md_file.endswith('.csv'):
            print(f"{librispeech_md_file} is not a csv file, continue.")
            continue
        # Get the name of the corresponding noise md file
        try:
            wham_md_file = [f for f in wham_md_files if
                            f.startswith(librispeech_md_file.split('-')[0])][0]
        except IndexError:
            print('Wham metadata are missing you can either generate the '
                  'missing wham files or add the librispeech metadata to '
                  'to_be_ignored list')
            break

        # Open .csv files from LibriSpeech
        librispeech_md = pd.read_csv(os.path.join(
            librispeech_md_dir, librispeech_md_file), engine='python')
        # Open .csv files from wham_noise
        wham_md = pd.read_csv(os.path.join(
            wham_md_dir, wham_md_file), engine='python')

        # # Filter out files that are shorter than 3s
        # num_samples = NUMBER_OF_SECONDS * RATE
        # librispeech_md = librispeech_md[
        #     librispeech_md['length'] >= num_samples]

        # Filenames
        save_path = os.path.join(md_dir,
                                 '_'.join([dataset, librispeech_md_file]))
        info_name = '_'.join([dataset, librispeech_md_file.strip('.csv'),
                              'info']) + '.csv'
        info_save_path = os.path.join(md_dir, info_name)
        print(f"Creating {os.path.basename(save_path)} file in {md_dir}")

        # Create dataframe
        mixtures_md, mixtures_info = create_librimix_df(
            librispeech_md, librispeech_dir, wham_md, wham_dir,
            n_src)
        # Round number of files
        mixtures_md = mixtures_md[:len(mixtures_md) // 100 * 100]
        mixtures_info = mixtures_info[:len(mixtures_info) // 100 * 100]

        # Save csv files
        mixtures_md.to_csv(save_path, index=False)
        mixtures_info.to_csv(info_save_path, index=False)


def check_already_generated(md_dir, dataset, to_be_ignored,
                            librispeech_md_files):
    # Check if the metadata files in LibriSpeech already have been used
    already_generated = os.listdir(md_dir)
    for generated in already_generated:
        if generated.startswith(f"{dataset}") and 'info' not in generated:
            if 'train-100' in generated:
                to_be_ignored.append('train-clean-100.csv')
            elif 'train-360' in generated:
                to_be_ignored.append('train-clean-360.csv')
            elif 'dev' in generated:
                to_be_ignored.append('dev-clean.csv')
            elif 'test' in generated:
                to_be_ignored.append('test-clean.csv')
            print(f"{generated} already exists in "
                  f"{md_dir} it won't be overwritten")
    for element in to_be_ignored:
        librispeech_md_files.remove(element)


def create_librimix_df(librispeech_md_file, librispeech_dir,
                       wham_md_file, wham_dir, n_src):
    """ Generate librimix dataframe from a LibriSpeech and wha md file"""

    # Create a dataframe that will be used to generate sources and mixtures
    mixtures_md = pd.DataFrame(columns=['mixture_ID'])
    # Create a dataframe with additional infos.
    mixtures_info = pd.DataFrame(columns=['mixture_ID'])
    # Add columns (depend on the number of sources)
    for i in range(n_src):
        mixtures_md[f"source_{i + 1}_path"] = {}
        mixtures_md[f"source_{i + 1}_gain"] = {}
        mixtures_info[f"speaker_{i + 1}_ID"] = {}
        mixtures_info[f"speaker_{i + 1}_sex"] = {}
    mixtures_md["noise_path"] = {}
    mixtures_md["noise_gain"] = {}
    # Generate pairs of sources to mix
    pairs = set_pairs(librispeech_md_file, n_src)
    # To each pair associate a noise
    pairs_noise = set_pairs_noise(pairs, wham_md_file)

    clip_counter = 0
    # For each combination create a new line in the dataframe
    for pair, pair_noise in tqdm(zip(pairs, pairs_noise), total=len(pairs)):
        # return infos about the sources, generate sources
        sources_info, sources_list_max = read_sources(
            librispeech_md_file, pair, n_src, librispeech_dir)
        # Add noise
        sources_info, sources_list_max = add_noise(
            wham_md_file, wham_dir, pair_noise, sources_list_max, sources_info)
        # compute initial loudness, randomize loudness and normalize sources
        loudness, _, sources_list_norm = set_loudness(sources_list_max)
        # Do the mixture
        mixture_max = mix(sources_list_norm)
        # Check the mixture for clipping and renormalize if necessary
        renormalize_loudness, did_clip = check_for_cliping(mixture_max,
                                                           sources_list_norm)
        clip_counter += int(did_clip)
        # Compute gain
        gain_list = compute_gain(loudness, renormalize_loudness)

        # Add information to the dataframe
        row_mixture, row_info = get_row(sources_info, gain_list, n_src)
        mixtures_md.loc[len(mixtures_md)] = row_mixture
        mixtures_info.loc[len(mixtures_info)] = row_info
    print(f"Among {len(mixtures_md)} mixtures, {clip_counter} clipped.")
    return mixtures_md, mixtures_info


def set_pairs(metadata_file, n_src):
    """ set pairs of sources to make the mixture """
    # Initialize list for pairs sources
    pair_list = []
    # A counter
    c = 0
    # Index of the rows in the metadata file
    index = list(range(len(metadata_file)))

    # Try to create pairs with different speakers end after 200 fails
    while len(index) >= n_src and c < 200:
        couple = random.sample(index, n_src)
        # Verify that speakers are different
        speaker_list = set([metadata_file.iloc[couple[i]]['speaker_ID']
                            for i in range(n_src)])
        # If there are duplicates then increment the counter
        if len(speaker_list) != n_src:
            c += 1
        # Else append the combination to L and erase the combination
        # from the available indexes
        else:
            for i in range(n_src):
                index.remove(couple[i])
            pair_list.append(couple)
            c = 0
    return pair_list


def set_pairs_noise(pairs, wham_md_file):
    # Initially take not augmented data
    md = wham_md_file[wham_md_file['augmented'] == 'False']
    # If there are more mixtures than noise than use augmented data
    if len(pairs) > len(md):
        md = wham_md_file
    # Associate a noise to a mixture
    pairs_noise = random.sample(list(md.index), len(pairs))

    return pairs_noise


def read_sources(metadata_file, pair, n_src, librispeech_dir):
    # Read lines corresponding to pair
    sources = [metadata_file.iloc[pair[i]] for i in range(n_src)]
    # Get sources info
    speaker_id_list = [source['speaker_ID'] for source in sources]
    sex_list = [source['sex'] for source in sources]
    length_list = [source['length'] for source in sources]
    path_list = [source['origin_path'] for source in sources]
    id_l = [os.path.split(source['origin_path'])[1].strip('.flac')
            for source in sources]
    mixtures_id = "_".join(id_l)

    # Get the longest and shortest source len
    max_length = max(length_list)
    sources_list = []

    # Read the source and compute some info
    for i in range(n_src):
        source = metadata_file.iloc[pair[i]]
        absolute_path = os.path.join(librispeech_dir,
                                     source['origin_path'])
        s, _ = sf.read(absolute_path, dtype='float32')
        sources_list.append(np.pad(s, (0, max_length - len(s))))

    sources_info = {'mixtures_id': mixtures_id,
                    'speaker_id_list': speaker_id_list, 'sex_list': sex_list,
                    'path_list': path_list}
    return sources_info, sources_list


def add_noise(wham_md_file, wham_dir, pair_noise, sources_list, sources_info):
    # Get the row corresponding to the index
    noise = wham_md_file.iloc[pair_noise]
    # Get the noise path
    noise_path = os.path.join(wham_dir, noise['origin_path'])
    # Read the noise
    n, _ = sf.read(noise_path, dtype='float32')
    # Keep the first channel
    if len(n.shape) > 1:
        n = n[:, 0]
    # Get expected length
    length = len(sources_list[0])
    # Pad if shorter
    if length > len(n):
        sources_list.append(np.pad(n, (0, length - len(n))))
    # Cut if longer
    else:
        sources_list.append(n[:length])
    # Get relative path
    sources_info['noise_path'] = noise_path
    return sources_info, sources_list


def set_loudness(sources_list):
    """ Compute original loudness and normalise them randomly """
    # Initialize loudness
    loudness_list = []
    # In LibriSpeech all sources are at 16KHz hence the meter
    meter = pyln.Meter(RATE)
    # Randomize sources loudness
    target_loudness_list = []
    sources_list_norm = []

    # Normalize loudness
    for i in range(len(sources_list)):
        # Compute initial loudness
        loudness_list.append(meter.integrated_loudness(sources_list[i]))
        # Pick a random loudness
        target_loudness = random.uniform(MIN_LOUDNESS, MAX_LOUDNESS)
        # Noise has a different loudness
        if i == len(sources_list) - 1:
            target_loudness = random.uniform(MIN_LOUDNESS - 5,
                                             MAX_LOUDNESS - 5)
        # Normalize source to target loudness

        with warnings.catch_warnings():
            # We don't want to pollute stdout, but we don't want to ignore
            # other warnings.
            warnings.simplefilter("ignore")
            src = pyln.normalize.loudness(sources_list[i], loudness_list[i],
                                          target_loudness)
        # If source clips, renormalize
        if np.max(np.abs(src)) >= 1:
            src = sources_list[i] * MAX_AMP / np.max(np.abs(sources_list[i]))
            target_loudness = meter.integrated_loudness(src)
        # Save scaled source and loudness.
        sources_list_norm.append(src)
        target_loudness_list.append(target_loudness)
    return loudness_list, target_loudness_list, sources_list_norm


def mix(sources_list_norm):
    """ Do the mixture for min mode and max mode """
    # Initialize mixture
    mixture_max = np.zeros_like(sources_list_norm[0])
    for i in range(len(sources_list_norm)):
        mixture_max += sources_list_norm[i]
    return mixture_max


def check_for_cliping(mixture_max, sources_list_norm):
    """Check the mixture (mode max) for clipping and re normalize if needed."""
    # Initialize renormalized sources and loudness
    renormalize_loudness = []
    clip = False
    # Recreate the meter
    meter = pyln.Meter(RATE)
    # Check for clipping in mixtures
    if np.max(np.abs(mixture_max)) > MAX_AMP:
        clip = True
        weight = MAX_AMP / np.max(np.abs(mixture_max))
    else:
        weight = 1
    # Renormalize
    for i in range(len(sources_list_norm)):
        new_loudness = meter.integrated_loudness(sources_list_norm[i] * weight)
        renormalize_loudness.append(new_loudness)
    return renormalize_loudness, clip


def compute_gain(loudness, renormalize_loudness):
    """ Compute the gain between the original and target loudness"""
    gain = []
    for i in range(len(loudness)):
        delta_loudness = renormalize_loudness[i] - loudness[i]
        gain.append(np.power(10.0, delta_loudness / 20.0))
    return gain


def get_row(sources_info, gain_list, n_src):
    """ Get new row for each mixture/info dataframe """
    row_mixture = [sources_info['mixtures_id']]
    row_info = [sources_info['mixtures_id']]
    for i in range(n_src):
        row_mixture.append(sources_info['path_list'][i])
        row_mixture.append(gain_list[i])
        row_info.append(sources_info['speaker_id_list'][i])
        row_info.append(sources_info['sex_list'][i])
    row_mixture.append(sources_info['noise_path'])
    row_mixture.append(gain_list[-1])
    return row_mixture, row_info


if __name__ == "__main__":
    args = parser.parse_args()
    main(args)