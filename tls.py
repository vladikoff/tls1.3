import os
import struct
import random
import socket
import iofree
import ciphers
from enum import IntEnum
from types import SimpleNamespace
from nacl.public import PrivateKey
from nacl.bindings import crypto_scalarmult


class Alert(Exception):
    ""


class MyIntEnum(IntEnum):
    @classmethod
    def from_value(cls, value):
        for e in cls:
            if e == value:
                return e
        raise Exception(f"Known {cls.__name__} type: {value}")


class UInt8Enum(MyIntEnum):
    def pack(self) -> bytes:
        return self.to_bytes(1, "big")


class UInt16Enum(MyIntEnum):
    def pack(self) -> bytes:
        return self.to_bytes(2, "big")


class HandshakeType(UInt8Enum):
    client_hello = 1
    server_hello = 2
    new_session_ticket = 4
    end_of_early_data = 5
    encrypted_extensions = 8
    certificate = 11
    certificate_request = 13
    certificate_verify = 15
    finished = 20
    key_update = 24
    message_hash = 254

    def pack_data(self, data: bytes) -> bytes:
        return self.pack() + pack_int(3, data)

    def tls_inner_plaintext(self, content: bytes) -> bytes:
        return (
            self.pack_data(content)
            + ContentType.handshake.pack()
            + (b"\x00" * random.randint(0, 10))
        )


def unpack_certificate_verify(mv):
    algorithm = int.from_bytes(mv[:2], "big")
    scheme = SignatureScheme.from_value(algorithm)
    signature_len = int.from_bytes(mv[2:4], "big")
    signature = mv[4 : 4 + signature_len]
    return SimpleNamespace(algorithm=scheme, signature=signature)


def unpack_new_session_ticket(mv):
    lifetime, age_add, nonce_len = struct.unpack_from("!IIB", mv)
    mv = mv[9:]
    nonce = bytes(mv[:nonce_len])
    mv = mv[nonce_len:]
    ticket_len = int.from_bytes(mv[:2], "big")
    mv = mv[2:]
    ticket = bytes(mv[:ticket_len])
    mv = mv[ticket_len:]
    extensions = ExtensionType.unpack_from(mv)
    return SimpleNamespace(
        lifetime=lifetime,
        age_add=age_add,
        nonce=nonce,
        ticket=ticket,
        extensions=extensions,
    )


class ExtensionType(UInt16Enum):
    server_name = 0
    max_fragment_length = 1
    status_request = 5
    supported_groups = 10
    signature_algorithms = 13
    use_srtp = 14
    heartbeat = 15
    application_layer_protocol_negotiation = 16
    signed_certificate_timestamp = 18
    client_certificate_type = 19
    server_certificate_type = 20
    padding = 21
    pre_shared_key = 41
    early_data = 42
    supported_versions = 43
    cookie = 44
    psk_key_exchange_modes = 45
    certificate_authorities = 47
    oid_filters = 48
    post_handshake_auth = 49
    signature_algorithms_cert = 50
    key_share = 51

    def pack_data(self, data: bytes) -> bytes:
        return self.pack() + pack_int(2, data)

    @classmethod
    def server_name_list(cls, host_name: str, *host_names: str) -> bytes:
        return cls.server_name.pack_data(
            pack_list(
                2,
                (
                    NameType.host_name.pack_data(name.encode())
                    for name in (host_name, *host_names)
                ),
            )
        )

    @classmethod
    def supported_versions_list(cls) -> bytes:
        return cls.supported_versions.pack_data(pack_int(1, b"\x03\x04"))

    @classmethod
    def supported_groups_list(cls, named_group, *named_groups) -> bytes:
        return cls.supported_groups.pack_data(
            pack_list(2, (group.pack() for group in (named_group, *named_groups)))
        )

    @classmethod
    def signature_algorithms_list(cls, algo, *algos) -> bytes:
        return cls.signature_algorithms.pack_data(
            pack_list(2, (alg.pack() for alg in (algo, *algos)))
        )

    @classmethod
    def unpack_from(cls, mv):
        extensions = {}
        while mv:
            value = int.from_bytes(mv[:2], "big")
            mv = mv[2:]
            if mv:
                extension_data_lenth = int.from_bytes(mv[:2], "big")
                pos = extension_data_lenth + 2
                extension_data = mv[2:pos]
                assert (
                    len(extension_data) == extension_data_lenth
                ), "extension length does not match"
                mv = mv[pos:]
            else:
                extension_data = b""
            et = cls.from_value(value)
            extensions[et] = et.unpack(extension_data)
        return extensions

    def unpack(self, data):
        if self == ExtensionType.supported_versions:
            return bytes(data)
        if self == ExtensionType.key_share:
            return NamedGroup.unpack_from(data)
        if self == ExtensionType.server_name:
            return data.decode()
        raise Exception("not support yet")


class ContentType(UInt8Enum):
    invalid = 0
    change_cipher_spec = 20
    alert = 21
    handshake = 22
    application_data = 23

    def tls_plaintext(self, data: bytes) -> bytes:
        assert len(data) > 0, "need data"
        data = memoryview(data)
        fragments = []
        while True:
            if len(data) > 16384:
                fragments.append(data[:16384])
                data = data[16384:]
            else:
                fragments.append(data)
                break

        return b"".join(
            (
                self.pack()
                + (
                    b"\x03\x01"
                    if i == 0 and self is ContentType.handshake
                    else b"\x03\x03"
                )
                + pack_int(2, fragment)
                for i, fragment in enumerate(fragments)
            )
        )

    def tls_inner_plaintext(self, content: bytes) -> bytes:
        return content + self.pack() + (b"\x00" * random.randint(0, 10))


class AlertLevel(UInt8Enum):
    warning = 1
    fatal = 2


class AlertDescription(UInt8Enum):
    close_notify = 0
    unexpected_message = 10
    bad_record_mac = 20
    record_overflow = 22
    handshake_failure = 40
    bad_certificate = 42
    unsupported_certificate = 43
    certificate_revoked = 44
    certificate_expired = 45
    certificate_unknown = 46
    illegal_parameter = 47
    unknown_ca = 48
    access_denied = 49
    decode_error = 50
    decrypt_error = 51
    protocol_version = 70
    insufficient_security = 71
    internal_error = 80
    inappropriate_fallback = 86
    user_canceled = 90
    missing_extension = 109
    unsupported_extension = 110
    unrecognized_name = 112
    bad_certificate_status_response = 113
    unknown_psk_identity = 115
    certificate_required = 116
    no_application_protocol = 120


class SignatureScheme(UInt16Enum):
    # RSASSA-PKCS1-v1_5 algorithms
    rsa_pkcs1_sha256 = 0x0401
    rsa_pkcs1_sha384 = 0x0501
    rsa_pkcs1_sha512 = 0x0601
    # ECDSA algorithms
    ecdsa_secp256r1_sha256 = 0x0403
    ecdsa_secp384r1_sha384 = 0x0503
    ecdsa_secp521r1_sha512 = 0x0603
    # RSASSA-PSS algorithms with public key OID rsaEncryption
    rsa_pss_rsae_sha256 = 0x0804
    rsa_pss_rsae_sha384 = 0x0805
    rsa_pss_rsae_sha512 = 0x0806
    # EdDSA algorithms
    ed25519 = 0x0807
    ed448 = 0x0808
    # RSASSA-PSS algorithms with public key OID RSASSA-PSS
    rsa_pss_pss_sha256 = 0x0809
    rsa_pss_pss_sha384 = 0x080a
    rsa_pss_pss_sha512 = 0x080b
    # Legacy algorithms
    rsa_pkcs1_sha1 = 0x0201
    ecdsa_sha1 = 0x0203
    # Reserved Code Points
    # private_use(0xFE00..0xFFFF)


# backend = default_backend()
dh_parameters = {
    # "ffdhe2048": dh.generate_parameters(generator=2, key_size=2048, backend=backend),
    # "ffdhe3072": dh.generate_parameters(generator=2, key_size=3072, backend=backend),
    # "ffdhe4096": dh.generate_parameters(generator=2, key_size=4096, backend=backend),
    # "ffdhe8192": dh.generate_parameters(generator=2, key_size=8192, backend=backend),
}


class NamedGroup(UInt16Enum):
    # Elliptic Curve Groups (ECDHE)
    secp256r1 = 0x0017
    secp384r1 = 0x0018
    secp521r1 = 0x0019
    x25519 = 0x001D
    x448 = 0x001E
    # Finite Field Groups (DHE)
    ffdhe2048 = 0x0100
    ffdhe3072 = 0x0101
    ffdhe4096 = 0x0102
    ffdhe6144 = 0x0103
    ffdhe8192 = 0x0104
    # Reserved Code Points
    # ffdhe_private_use(0x01FC..0x01FF)
    # ecdhe_private_use(0xFE00..0xFEFF)

    # def dh_key_share_entry(self):
    #     private_key = dh_parameters[self.name].generate_private_key()
    #     peer_public_key = private_key.public_key()
    #     opaque = peer_public_key.public_bytes(
    #         Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    #     )
    #     return private_key, self.pack() + pack_int(2, opaque)

    @classmethod
    def new_x25519(cls):
        private_key = PrivateKey.generate()
        key_exchange = bytes(private_key.public_key)
        return private_key, cls.x25519.pack() + pack_int(2, key_exchange)

    @classmethod
    def unpack_from(cls, data):
        value = int.from_bytes(data[:2], "big")
        group_type = cls.from_value(value)
        length = int.from_bytes(data[2:4], "big")
        assert length == len(data[4:]), "group length does not match"
        key_exchange = bytes(data[4:])
        return KeyShareEntry(group_type, key_exchange)


class KeyShareEntry:
    __slots__ = ("group", "key_exchange")

    def __init__(self, group, key_exchange):
        self.group = group
        self.key_exchange = key_exchange

    def __repr__(self):
        return f"{self.__class__.__name__}(group={self.group!r},key_exchange={self.key_exchange})"

    def pack(self):
        return self.group.pack() + pack_int(2, self.key_exchange)


class CertificateType(UInt8Enum):
    X509 = 0
    RawPublicKey = 2


class CertificateEntry:
    __slots__ = ("cert_type", "cert_data", "extensions")

    def __init__(self, cert_type, cert_data, extensions):
        self.cert_type = cert_type
        self.cert_data = cert_data
        self.extensions = extensions

    def __repr__(self):
        return f"{self.__class__.__name__}(type={self.cert_type!r},extensions={self.extensions})"

    @classmethod
    def unpack_from(cls, data):
        certificate_request_context_len = data[0]
        certificate_request_context = data[1 : 1 + certificate_request_context_len]
        certificate_request_context
        data = data[1 + certificate_request_context_len :]
        certificate_list_len = int.from_bytes(data[:3], "big")
        certificate_list = data[3 : 3 + certificate_list_len]
        assert (
            len(data[3 + certificate_list_len :]) == 0
        ), "Certificate length does not match"
        cert_type = CertificateType.from_value(certificate_list[0])
        cert_data_len = int.from_bytes(certificate_list[1:4], "big")
        cert_data = certificate_list[4 : 4 + cert_data_len]
        extensions_data = certificate_list[4 + cert_data_len :]
        extensions_len = int.from_bytes(extensions_data[:2], "big")
        extensions = extensions_data[2 : 2 + extensions_len]
        assert (
            len(extensions_data[2 + extensions_len :]) == 0
        ), "extensions length does not match"
        return cls(cert_type, cert_data, extensions)


class KeyUpdateRequest(UInt8Enum):
    update_not_requested = 0
    update_requested = 1


class PskKeyExchangeMode(UInt8Enum):
    psk_ke = 0
    psk_dhe_ke = 1


class CipherSuite(UInt16Enum):
    TLS_AES_128_GCM_SHA256 = 0x1301
    TLS_AES_256_GCM_SHA384 = 0x1302
    TLS_CHACHA20_POLY1305_SHA256 = 0x1303
    TLS_AES_128_CCM_SHA256 = 0x1304
    TLS_AES_128_CCM_8_SHA256 = 0x1305

    @classmethod
    def all(cls) -> set:
        if not hasattr(cls, "_all"):
            cls._all = {suite.pack() for suite in cls}
        return cls._all

    @classmethod
    def select(cls, data):
        data = memoryview(data)
        for i in (0, len(data), 2):
            if data[i : i + 2] in cls.all():
                return data[i : i + 2].tobytes()

    @classmethod
    def get_cipher(cls, data):
        value = int.from_bytes(data, "big")
        if value == cls.TLS_AES_128_GCM_SHA256:
            return ciphers.TLS_AES_128_GCM_SHA256
        elif value == cls.TLS_AES_256_GCM_SHA384:
            return ciphers.TLS_AES_256_GCM_SHA384
        elif value == cls.TLS_AES_128_CCM_SHA256:
            return ciphers.TLS_AES_128_CCM_SHA256
        elif value == cls.TLS_AES_128_CCM_8_SHA256:
            return ciphers.TLS_AES_128_CCM_8_SHA256
        elif value == cls.TLS_CHACHA20_POLY1305_SHA256:
            return ciphers.TLS_CHACHA20_POLY1305_SHA256
        else:
            raise Exception("bad cipher suite")

    @classmethod
    def pack_all(cls):
        return pack_all(
            2,
            [
                cls.TLS_AES_128_GCM_SHA256,
                cls.TLS_AES_256_GCM_SHA384,
                cls.TLS_AES_128_CCM_SHA256,
                cls.TLS_CHACHA20_POLY1305_SHA256,
            ],
        )


class NameType(UInt8Enum):
    host_name = 0

    def pack_data(self, data: bytes) -> bytes:
        return self.pack() + pack_int(2, data)


def pack_int(length: int, data: bytes) -> bytes:
    return len(data).to_bytes(length, "big") + data


def pack_list(length: int, iterable) -> bytes:
    return pack_int(length, b"".join(data for data in iterable))


def pack_all(length: int, iterable) -> bytes:
    return pack_int(length, b"".join(obj.pack() for obj in iterable))


class Const:
    all_signature_algorithms = ExtensionType.signature_algorithms.pack_data(
        pack_all(2, SignatureScheme)
    )
    all_supported_groups = ExtensionType.supported_groups.pack_data(
        pack_all(2, [NamedGroup.x25519])
    )


def client_hello_pack(
    extensions, cipher_suites=None, compatibility_mode=True, retry=False
):
    legacy_version = b"\x03\x03"
    if compatibility_mode:
        legacy_session_id = os.urandom(32)
    else:
        legacy_session_id = b""
    if cipher_suites is None:
        cipher_suites = CipherSuite.pack_all()
    else:
        cipher_suites = pack_list(
            2, (cipher_suite.pack() for cipher_suite in cipher_suites)
        )
    assert 0 < len(cipher_suites) < 32768, "cipher_suites<2..2^16-2>"
    randbytes = bytes.fromhex(
        "CF21AD74E59A6111BE1D8C021E65B891C2A211167ABB8C5E079E09E2C8A8339C"
    )

    msg = b"".join(
        (
            legacy_version,
            randbytes if retry else os.urandom(32),
            pack_int(1, legacy_session_id),
            cipher_suites,
            b"\x01\x00",  # legacy_compression_methods
            pack_list(2, extensions),
        )
    )
    return HandshakeType.client_hello.pack_data(msg)


def server_hello_pack(legacy_session_id_echo, cipher_suite, extensions) -> bytes:
    legacy_version = b"\x03\x03"
    msg = b"".join(
        (
            legacy_version,
            os.urandom(32),
            pack_int(1, legacy_session_id_echo),
            cipher_suite.pack(),
            b"\x00",
        )
    )
    return ContentType.handshake.tls_plaintext(
        HandshakeType.server_hello.pack_data(msg)
    )


def key_share_client_hello_pack(*key_share_entries):
    return ExtensionType.key_share.pack_data(pack_list(2, key_share_entries))


def client_pre_shared_key_pack(identities, binders):
    return b"".join(identities) + b"".join(binders)


def _PskIdentity(identity: bytes, obfuscated_ticket_age: int):
    return pack_int(2, identity) + obfuscated_ticket_age.to_bytes(4, "big")


def _PskBinderEntry(data: bytes):
    return pack_int(1, data)


class TLSClient:
    def __init__(self):
        self.private_key, key_share_entry = NamedGroup.new_x25519()
        self.client_hello_data = client_hello_pack(
            [
                ExtensionType.server_name_list("127.0.0.1"),
                # ExtensionType.server_name_list("localhost"),
                ExtensionType.supported_versions_list(),
                Const.all_signature_algorithms,
                Const.all_supported_groups,
                key_share_client_hello_pack(key_share_entry),
            ]
        )
        self.handshake_context = [self.client_hello_data]
        self.server_finished = False

    def unpack_server_hello(self, mv: memoryview):
        assert mv[:2] == b"\x03\x03", "version must be 0x0303"
        random = bytes(mv[2:34])
        legacy_session_id_echo_length = mv[34]
        legacy_session_id_echo = bytes(mv[35 : 35 + legacy_session_id_echo_length])
        mv = mv[35 + legacy_session_id_echo_length :]
        cipher_suite = CipherSuite.get_cipher(mv[:2])
        assert mv[2] == 0, "legacy_compression_method should be 0"
        extension_length = int.from_bytes(mv[3:5], "big")
        extensions_mv = mv[5:]
        assert (
            len(extensions_mv) == extension_length
        ), "extensions length does not match"
        extensions = ExtensionType.unpack_from(extensions_mv)
        return SimpleNamespace(
            handshake_type=HandshakeType.server_hello,
            random=random,
            legacy_session_id_echo=legacy_session_id_echo,
            cipher_suite=cipher_suite,
            extensions=extensions,
        )

    def unpack_handshake(self, mv: memoryview):
        handshake_type = mv[0]
        length = int.from_bytes(mv[1:4], "big")
        assert len(mv[4:]) == length, f"handshake length does not match"
        handshake_data = mv[4:]
        if handshake_type == HandshakeType.server_hello:
            self.handshake_context.append(mv)
            return self.unpack_server_hello(handshake_data)
        elif handshake_type == HandshakeType.encrypted_extensions:
            self.handshake_context.append(mv)
            self.encrypted_extensions = ExtensionType.unpack_from(handshake_data)
        elif handshake_type == HandshakeType.certificate_request:
            self.handshake_context.append(mv)
        elif handshake_type == HandshakeType.certificate:
            self.handshake_context.append(mv)
            self.certificate_entry = CertificateEntry.unpack_from(handshake_data)
        elif handshake_type == HandshakeType.certificate_verify:
            self.handshake_context.append(mv)
            self.certificate_verify = unpack_certificate_verify(handshake_data)
        elif handshake_type == HandshakeType.finished:
            context = b"".join(self.handshake_context)
            assert handshake_data == self.peer_cipher.verify_data(
                context
            ), "server handshake finished does not match"
            self.handshake_context.append(mv)
            self.server_finished = True
        elif handshake_type == HandshakeType.new_session_ticket:
            self.session_ticket = unpack_new_session_ticket(mv)
        else:
            raise Exception(f"unknown handshake type {handshake_type}")

    def get_context(self):
        return b"".join(self.handshake_context)

    def tls_response(self):
        while True:
            head = yield from iofree.read(5)
            assert head[1:3] == b"\x03\x03", f"bad legacy_record_version {head[1:3]}"
            length = int.from_bytes(head[3:], "big")
            content = memoryview((yield from iofree.read(length)))
            if head[0] == ContentType.alert:
                level = AlertLevel.from_value(content[0])
                description = AlertDescription.from_value(content[1])
                raise Alert(level, description)
            elif head[0] == ContentType.handshake:
                self.peer_handshake = self.unpack_handshake(content)
                assert (
                    self.peer_handshake.handshake_type == HandshakeType.server_hello
                ), "expect server hello"
                peer_pk = self.peer_handshake.extensions[
                    ExtensionType.key_share
                ].key_exchange
                shared_key = crypto_scalarmult(bytes(self.private_key), peer_pk)
                TLSCipher = self.peer_handshake.cipher_suite
                key_scheduler = TLSCipher.tls_hash.scheduler(shared_key)
                secret = key_scheduler.server_handshake_traffic_secret(
                    self.get_context()
                )
                # server handshake cipher
                self.peer_cipher = TLSCipher(secret)
                client_handshake_traffic_secret = key_scheduler.client_handshake_traffic_secret(
                    self.get_context()
                )
            elif head[0] == ContentType.application_data:
                plaintext = self.peer_cipher.decrypt(content, head).rstrip(b"\x00")
                content_type = ContentType.from_value(plaintext[-1])
                if content_type == ContentType.handshake:
                    self.unpack_handshake(plaintext[:-1])
                    if self.server_finished:
                        # client handshake cipher
                        self.cipher = TLSCipher(client_handshake_traffic_secret)
                        context = b"".join(self.handshake_context)
                        client_finished = self.cipher.verify_data(context)
                        inner_plaintext = HandshakeType.finished.tls_inner_plaintext(
                            client_finished
                        )
                        record = self.cipher.tls_ciphertext(inner_plaintext)
                        change_cipher_spec = ContentType.change_cipher_spec.tls_plaintext(
                            b"\x01"
                        )
                        yield from iofree.write(change_cipher_spec + record)
                        # server application cipher
                        server_secret = key_scheduler.server_application_traffic_secret_0(
                            self.get_context()
                        )
                        self.peer_cipher = TLSCipher(server_secret)
                        self.server_finished = False

                        # client application cipher
                        client_secret = key_scheduler.client_application_traffic_secret_0(
                            self.get_context()
                        )
                        self.cipher = TLSCipher(client_secret)

                elif content_type == ContentType.application_data:
                    yield from iofree.write(plaintext[:-1])
                elif content_type == ContentType.alert:
                    level = AlertLevel.from_value(plaintext[0])
                    description = AlertDescription.from_value(plaintext[1])
                    raise Alert(level, description)
                elif content_type == ContentType.invalid:
                    raise Exception("invalid content type")
                else:
                    raise Exception(f"unexpected content type {content_type}")
            elif head[0] == ContentType.change_cipher_spec:
                assert content == b"\x01", "change_cipher should be 0x01"
            else:
                raise Exception(f"Unknown content type: {head[0]}")

    def pack_application_data(self, payload: bytes):
        inner_plaintext = ContentType.application_data.tls_inner_plaintext(payload)
        return self.cipher.tls_ciphertext(inner_plaintext)

    def pack_alert(self, description: AlertDescription, warning: bool = True):
        level = AlertLevel.warning if warning else AlertLevel.fatal
        payload = level.pack() + description.pack()
        inner_plaintext = ContentType.alert.tls_inner_plaintext(payload)
        return self.cipher.tls_ciphertext(inner_plaintext)


client = TLSClient()


sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("127.0.0.1", 1799))
sock.sendall(ContentType.handshake.tls_plaintext(client.client_hello_data))
server_data = sock.recv(4096)

parser = iofree.Parser(client.tls_response())
parser.send(server_data)
sock.sendall(parser.read())
sock.sendall(client.pack_application_data(b"ping\n"))
server_data = sock.recv(4096)
parser.send(server_data)

server_data = sock.recv(4096)
parser.send(server_data)

for i in range(3):
    server_data = sock.recv(4096)
    parser.send(server_data)
    data = parser.read()
    print(data)


sock.sendall(client.pack_alert(AlertDescription.close_notify))
sock.close()
