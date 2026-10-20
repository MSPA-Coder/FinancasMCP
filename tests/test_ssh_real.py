"""Handshake, pinagem e reconexão contra servidor SSH efêmero no contêiner."""

import asyncio
from contextlib import suppress

import asyncssh
import pytest

from financas_mcp import conexao
from financas_mcp.config import Banco, Config, Ssh


class Servidor(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def public_key_auth_supported(self):
        return True

    def validate_public_key(self, username, key):
        return key == self.chave_cliente

    def connection_requested(self, dest_host, dest_port, orig_host, orig_port):
        return True


@pytest.mark.parametrize("falha", [None, "chave_host", "chave_cliente"])
def test_tunel_real_cache_concorrente_reconexao_e_pinagem(tmp_path, monkeypatch, falha):
    monkeypatch.setenv("LOGNAME", "qa")
    async def executar():
        chave_servidor = asyncssh.generate_private_key("ssh-ed25519")
        chave_cliente = asyncssh.generate_private_key("ssh-ed25519")
        caminho = tmp_path / "chave"
        chave_cliente.write_private_key(caminho)

        def fabrica():
            servidor = Servidor()
            servidor.chave_cliente = chave_cliente.convert_to_public()
            return servidor

        async def eco(reader, writer):
            try:
                writer.write(await reader.read(100))
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        servidor_eco = await asyncio.start_server(eco, "127.0.0.1", 0)
        porta_eco = servidor_eco.sockets[0].getsockname()[1]
        servidor = await asyncssh.create_server(fabrica, "127.0.0.1", 0, server_host_keys=[chave_servidor])
        conectar_real = asyncssh.connect

        async def conectar(host, **opcoes):
            # Somente o endereço de transporte é redirecionado ao servidor de teste.
            return await conectar_real(host, port=servidor.get_port(), **opcoes)

        monkeypatch.setattr(conexao.asyncssh, "connect", conectar)
        if falha == "chave_cliente":
            asyncssh.generate_private_key("ssh-ed25519").write_private_key(caminho)
        pin = chave_servidor if falha != "chave_host" else asyncssh.generate_private_key("ssh-ed25519")
        ssh = Ssh("127.0.0.1", "qa", str(caminho), pin.export_public_key().decode().strip())
        banco = Banco(porta_eco, "qa", "qa", "sintetica")
        gerente = conexao.Conexoes(Config("sintetico", ssh, {"cb": banco, "crv": banco}))
        try:
            if falha:
                erro = asyncssh.HostKeyNotVerifiable if falha == "chave_host" else asyncssh.PermissionDenied
                with pytest.raises(erro):
                    await gerente._porta_local("cb", banco)
                assert gerente._portas == {}
                return
            portas = await asyncio.gather(*(gerente._porta_local("cb", banco) for _ in range(8)))
            assert len(set(portas)) == 1
            porta_crv = await gerente._porta_local("crv", banco)
            assert porta_crv != portas[0]
            for porta in (portas[0], porta_crv):
                reader, writer = await asyncio.open_connection("127.0.0.1", porta)
                writer.write(b"consulta-sintetica")
                await writer.drain()
                assert await reader.read(100) == b"consulta-sintetica"
                writer.close()
                await writer.wait_closed()
            anterior = gerente._ssh
            anterior.abort()
            await anterior.wait_closed()
            nova = await gerente._porta_local("cb", banco)
            assert gerente._ssh is not anterior
            assert gerente._portas == {"cb": nova}
            reader, writer = await asyncio.open_connection("127.0.0.1", nova)
            writer.write(b"apos-reconexao")
            await writer.drain()
            assert await reader.read(100) == b"apos-reconexao"
            writer.close()
            await writer.wait_closed()
        finally:
            if gerente._ssh:
                gerente._ssh.close()
                with suppress(asyncssh.Error):
                    await gerente._ssh.wait_closed()
            servidor.close()
            await servidor.wait_closed()
            servidor_eco.close()
            await servidor_eco.wait_closed()

    asyncio.run(asyncio.wait_for(executar(), timeout=15))
