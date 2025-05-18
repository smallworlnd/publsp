import click
import json
import logging
import os
from datetime import datetime
from nostr_sdk import Keys, EncryptedSecretKey

from publsp.settings import EnvironmentSettings, NostrSettings

logger = logging.getLogger(name=__name__)
NOSTR_KEYS_FILE = NostrSettings().nostr_keys_path \
    if EnvironmentSettings().environment == 'production' \
    else NostrSettings().nostr_keys_path_dev


class KeyHandler:
    """
    Create and manage nostr keys either for the lsp customer and the lsp
    itself.

    Users can add their own preconstructed keys to the keys file.

    TODO: nsec is kept in memory unencrypted for the duration of the script,
    need to harden this
    """
    def __init__(
            self,
            client: str,
            reuse_keys: bool = NostrSettings().reuse_keys,
            write_keys: bool = NostrSettings().write_keys,
            ask_encrypt: bool = NostrSettings().ask_encrypt,
            filename: str = NOSTR_KEYS_FILE):
        self.filename = filename
        if reuse_keys:
            self.keys = self.read_keys(client=client)
            if not self.keys:
                self.keys = self.generate_keys(
                    client=client,
                    write_keys=write_keys,
                    ask_encrypt=ask_encrypt)
        else:
            self.keys = self.generate_keys(
                client=client,
                write_keys=write_keys,
                ask_encrypt=ask_encrypt)

    def generate_keys(self, client: str, write_keys: bool, ask_encrypt: bool):
        """
        Generate a new set of keys for either server or client.  Asks for user
        input to encrypt the keys or not, and to choose a password if the
        former.
        """
        keys = Keys.generate()
        pubkey = keys.public_key().to_bech32()
        privkey = keys.secret_key().to_bech32()

        if ask_encrypt:
            # Ask the user if they want to set a password
            set_password = click.confirm(
                'Do you want to encrypt your new nsec?',
                default=True
            )
            if set_password:
                password = click.prompt(
                    "Enter your password", hide_input=True
                )
                logger.info("Encrypting keys and saving to file...")
                privkey = keys.secret_key().encrypt(password=password).to_bech32()
                logger.info("Remember to keep your password in a safe place!")
            else:
                logger.info("nsec will be saved in plaintext; highly discouraged!")

        if write_keys:
            self.write_keys(privkey, pubkey, client)
        return keys

    def write_keys(self, privkey, pubkey, client: str):
        """Write a new key to the JSON file."""
        new_key = {
            'timestamp': datetime.now().isoformat(),
            'privkey': privkey,
            'pubkey': pubkey,
            'note': None
        }

        if os.path.exists(self.filename):
            with open(self.filename, 'r') as file:
                data = json.load(file)
        else:
            data = {'keys': {'lsp': [], 'customer': []}}
            logger.debug("created new file and initialized key structure.")

        if client in data['keys']:
            data['keys'][client].append(new_key)
            logger.debug(f"appended new key to existing '{client}' category.")
        else:
            logger.error(f"invalid client '{client}' specified. Key not added.")

        with open(self.filename, 'w') as file:
            json.dump(data, file, indent=4)
            logger.info(f'Keys written to {self.filename}')

    def read_keys(self, client: str):
        """Read the latest key from the JSON file for a specified client."""
        if os.path.exists(self.filename):
            with open(self.filename, 'r') as file:
                data = json.load(file)

            if client in data.get('keys', {}) and data['keys'][client]:
                latest = max(data['keys'][client], key=lambda x: x['timestamp'])
                priv = latest['privkey']
                if 'ncryptsec' in priv:
                    password = click.prompt(
                        "Found encrypted nsec, enter password to decrypt", hide_input=True
                    )
                    keys = Keys.parse(
                        EncryptedSecretKey\
                            .from_bech32(priv)\
                            .decrypt(password=password)\
                            .to_bech32()
                    )
                else:
                    keys = Keys.parse(priv)
                logger.debug(f"retrieved latest key for {client}")
                return keys
            else:
                logger.debug(f"no keys found for {client} in {self.filename}")
                return None
        else:
            logger.debug(f"could not find keys file {self.filename}")
            return None
