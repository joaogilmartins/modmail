import asyncio
from operator import truediv
import re
from datetime import datetime
from itertools import zip_longest
from typing import Optional, Union
from types import SimpleNamespace

import discord
from discord.ext import commands
from discord.ext.commands.cooldowns import BucketType
from discord.role import Role
from discord.utils import escape_markdown

from dateutil import parser
from natural.date import duration

from core import checks
from core.models import DMDisabled, PermissionLevel, SimilarCategoryConverter, getLogger
from core.paginator import EmbedPaginatorSession
from core.thread import Thread
from core.time import UserFriendlyTime, human_timedelta
from core.utils import *

logger = getLogger(__name__)


class Modmail(commands.Cog):
    """Comandos diretamente relacionados com a funcionalidade geral do bot de Suporte."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @trigger_typing
    @checks.has_permissions(PermissionLevel.OWNER)
    async def setup(self, ctx):
        """
        Setup - Suporte.

        Você só precisará usar esse comando
        depois de ter configurado o modmail.
        """

        if ctx.guild != self.bot.modmail_guild:
            return await ctx.send(
                f"Você apenas pode dar setup em: {self.bot.modmail_guild}."
            )

        if self.bot.main_category is not None:
            logger.debug("main_category encontrada. Não pode haver resetup.")
            return await ctx.send(f"{self.bot.modmail_guild} já foi setado.")

        if self.bot.modmail_guild is None:
            embed = discord.Embed(
                title="Erro",
                description="Guild não encontrada.",
                color=self.bot.error_color,
            )
            return await ctx.send(embed=embed)

        overwrites = {
            self.bot.modmail_guild.default_role: discord.PermissionOverwrite(read_messages=False),
            self.bot.modmail_guild.me: discord.PermissionOverwrite(read_messages=True),
        }

        for level in PermissionLevel:
            if level <= PermissionLevel.REGULAR:
                continue
            permissions = self.bot.config["level_permissions"].get(level.name, [])
            for perm in permissions:
                perm = int(perm)
                if perm == -1:
                    key = self.bot.modmail_guild.default_role
                else:
                    key = self.bot.modmail_guild.get_member(perm)
                    if key is None:
                        key = self.bot.modmail_guild.get_role(perm)
                if key is not None:
                    logger.info("Garantindo permissão %s.", key.name)
                    overwrites[key] = discord.PermissionOverwrite(read_messages=True)

        category = await self.bot.modmail_guild.create_category(
            name="Modmail", overwrites=overwrites
        )

        await category.edit(position=0)

        log_channel = await self.bot.modmail_guild.create_text_channel(
            name="bot-logs", category=category
        )

        embed = discord.Embed(
            title="Recomendação amigável",
            description=f"Você pode usar `{self.bot.prefix}config set log_channel_id "
            "<channel-id>` para configurar outro canal e apagar este correntemente "
            f"{log_channel.mention} log channel.",
            color=self.bot.main_color,
        )

        embed.add_field(
            name="Obrigado por usar o bot!",
            value="If you like what you see, consider giving the "
            "[repo a star](https://github.com/kyb3r/modmail) :star: and if you are "
            "feeling extra generous, buy us coffee on [Patreon](https://patreon.com/kyber) :heart:!",
        )

        embed.set_footer(text=f'Type "{self.bot.prefix}help" for a complete list of commands.')
        await log_channel.send(embed=embed)

        self.bot.config["main_category_id"] = category.id
        self.bot.config["log_channel_id"] = log_channel.id

        await self.bot.config.update()
        await ctx.send(
            "**Servidor configurado com sucesso.**\n"
            "Considere setar níveis de permissão para acesso a comandos "
            "ou habilidade dos utilizadores para usar modmail.\n\n"
            f"Type:\n- `{self.bot.prefix}permissions` e `{self.bot.prefix}permissions add` "
            "para mais informação a setar permissões.\n"
            f"- `{self.bot.prefix}config help` para uma lista de customizações possíveis."
        )

        if not self.bot.config["command_permissions"] and not self.bot.config["level_permissions"]:
            await self.bot.update_perms(PermissionLevel.REGULAR, -1)
            for owner_id in self.bot.bot_owner_ids:
                await self.bot.update_perms(PermissionLevel.OWNER, owner_id)

    @commands.group(aliases=["snippets"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet(self, ctx, *, name: str.lower = None):
        """
        Cria mensagens pré-definidas nas threads.

        Quando `{prefix}snippet` é usado por si só, irá providenciar
        uma lista de snippets em utilizacão. `{prefix}snippet-name` mostrará o que
        snippet aponta para.

        Para criar um snippet:
        - `{prefix}snippet e snippet-name Texto.`

        Você só pode usar um snippet num canal de thread
        com `{prefix}snippet-name`, a mensagem "Texto"
        será mandada para o recipiente.

        - `{prefix}alias add snippet-name anonreply Para snippets anonimos.`

        Veja também `{prefix}alias`.
        """

        if name is not None:
            val = self.bot.snippets.get(name)
            if val is None:
                embed = create_not_found_embed(name, self.bot.snippets.keys(), "Snippet")
            else:
                embed = discord.Embed(
                    title=f'Snippet - "{name}":', description=val, color=self.bot.main_color
                )
            return await ctx.send(embed=embed)

        if not self.bot.snippets:
            embed = discord.Embed(
                color=self.bot.error_color, description="Você não tem nenhuns snippets no momento."
            )
            embed.set_footer(text=f'Check "{self.bot.prefix}help snippet add" to add a snippet.')
            embed.set_author(name="Snippets", icon_url=ctx.guild.icon_url)
            return await ctx.send(embed=embed)

        embeds = []

        for i, names in enumerate(zip_longest(*(iter(sorted(self.bot.snippets)),) * 15)):
            description = format_description(i, names)
            embed = discord.Embed(color=self.bot.main_color, description=description)
            embed.set_author(name="Snippets", icon_url=ctx.guild.icon_url)
            embeds.append(embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @snippet.command(name="raw")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet_raw(self, ctx, *, name: str.lower):
        """
        Veja o conteúdo de um snippet.
        """
        val = self.bot.snippets.get(name)
        if val is None:
            embed = create_not_found_embed(name, self.bot.snippets.keys(), "Snippet")
        else:
            val = truncate(escape_code_block(val), 2048 - 7)
            embed = discord.Embed(
                title=f'Raw snippet - "{name}":',
                description=f"```\n{val}```",
                color=self.bot.main_color,
            )

        return await ctx.send(embed=embed)

    @snippet.command(name="add")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet_add(self, ctx, name: str.lower, *, value: commands.clean_content):
        """
        Adiciona um snippet.

        Para adicionar um snippet, faça: ```
        {prefix}snippet add oi :)
        ```
        assim, quando fizer `{prefix}hey`, "oi :)" será mandado para o recipiente

        Para adicionar um snippet com várias palavras: ```
        {prefix}snippet add "duas palavras" isto é um snippet com duas paalvras.
        ```
        """
        if name in self.bot.snippets:
            embed = discord.Embed(
                title="Error",
                color=self.bot.error_color,
                description=f"Snippet `{name}` já existe.",
            )
            return await ctx.send(embed=embed)

        if name in self.bot.aliases:
            embed = discord.Embed(
                title="Erro",
                color=self.bot.error_color,
                description=f"Um alias que compartilha o mesmo nome já existe: `{name}`.",
            )
            return await ctx.send(embed=embed)

        if len(name) > 120:
            embed = discord.Embed(
                title="Erro",
                color=self.bot.error_color,
                description="Snippet não podem ser maiores que 120 caracteres.",
            )
            return await ctx.send(embed=embed)

        self.bot.snippets[name] = value
        await self.bot.config.update()

        embed = discord.Embed(
            title="Snippet adicionado",
            color=self.bot.main_color,
            description="Snippet criado com sucesso.",
        )
        return await ctx.send(embed=embed)

    @snippet.command(name="remove", aliases=["del", "delete"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet_remove(self, ctx, *, name: str.lower):
        """Remove um snippet."""

        if name in self.bot.snippets:
            embed = discord.Embed(
                title="Snippet removido",
                color=self.bot.main_color,
                description=f"Snippet `{name}` is now deleted.",
            )
            self.bot.snippets.pop(name)
            await self.bot.config.update()
        else:
            embed = create_not_found_embed(name, self.bot.snippets.keys(), "Snippet")
        await ctx.send(embed=embed)

    @snippet.command(name="edit")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet_edit(self, ctx, name: str.lower, *, value):
        """
        Edita um snippet.

        Para editar um snippet com várias palavras: ```
        {prefix}snippet edit "oi :)" isto é um novo snippet.
        ```
        """
        if name in self.bot.snippets:
            self.bot.snippets[name] = value
            await self.bot.config.update()

            embed = discord.Embed(
                title="Snippet editado",
                color=self.bot.main_color,
                description=f'`{name}` will now send "{value}".',
            )
        else:
            embed = create_not_found_embed(name, self.bot.snippets.keys(), "Snippet")
        await ctx.send(embed=embed)

    @commands.command(usage="<category> [options]")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @checks.thread_only()
    async def move(self, ctx, *, arguments):
        """
        Move a thread para outra categoria.

        `category` pode ser um ID de categoria ou nome.
        `options` serve para adicionar argumentos a realizar.
        """
        split_args = arguments.strip('"').split(" ")
        category = None

        # manually parse arguments, consumes as much of args as possible for category
        for i in range(len(split_args)):
            try:
                if i == 0:
                    fmt = arguments
                else:
                    fmt = " ".join(split_args[:-i])

                category = await SimilarCategoryConverter().convert(ctx, fmt)
            except commands.BadArgument:
                if i == len(split_args) - 1:
                    # last one
                    raise
                pass
            else:
                break

        if not category:
            raise commands.ChannelNotFound(arguments)

        options = " ".join(arguments.split(" ")[-i:])

        thread = ctx.thread
        silent = False

        if options:
            silent_words = ["silent", "silently"]
            silent = any(word in silent_words for word in options.split())

        await thread.channel.edit(category=category, sync_permissions=True)

        if self.bot.config["thread_move_notify"] and not silent:
            embed = discord.Embed(
                title=self.bot.config["thread_move_title"],
                description=self.bot.config["thread_move_response"],
                color=self.bot.main_color,
            )
            await thread.recipient.send(embed=embed)

        if self.bot.config["thread_move_notify_mods"]:
            mention = self.bot.config["mention"]
            await thread.channel.send(f"{mention}, thread foi movida.")

        sent_emoji, _ = await self.bot.retrieve_emoji()
        await self.bot.add_reaction(ctx.message, sent_emoji)

    async def send_scheduled_close_message(self, ctx, after, silent=False):
        human_delta = human_timedelta(after.dt)

        silent = "*silently* " if silent else ""

        embed = discord.Embed(
            title="Fecho calendarizado",
            description=f"Esta thread vai fechar {silent}em {human_delta}.",
            color=self.bot.error_color,
        )

        if after.arg and not silent:
            embed.add_field(name="Message", value=after.arg)

        embed.set_footer(text="Calendarização será cancelada se uma mensagem for enviada")
        embed.timestamp = after.dt

        await ctx.send(embed=embed)

    @commands.command(usage="[after] [close message]")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def close(self, ctx, *, after: UserFriendlyTime = None):
        """
        Fecha a thread atual.

        Fechar depois de um período de tempo (em inglês):
        - `{prefix}close in 5 hours`
        - `{prefix}close 2m30s`

        Mensagens programadas:
        - `{prefix}close 2 hours Problema resolvido.`
        - `{prefix}close Será contactado assim que soubermos mais.`

        Fecha uma thread silenciosamente (sem mensagem)
        - `{prefix}close silently`
        - `{prefix}close in 10m silently`

        Cancelar a calendarização:
        - `{prefix}close cancel`
        """

        thread = ctx.thread

        now = datetime.utcnow()

        close_after = (after.dt - now).total_seconds() if after else 0
        message = after.arg if after else None
        silent = str(message).lower() in {"silent", "silently"}
        cancel = str(message).lower() == "cancel"

        if cancel:

            if thread.close_task is not None or thread.auto_close_task is not None:
                await thread.cancel_closure(all=True)
                embed = discord.Embed(
                    color=self.bot.error_color, description="Calendarização do fecho cancelada."
                )
            else:
                embed = discord.Embed(
                    color=self.bot.error_color,
                    description="Esta thread ainda não foi calendarizada para fechar.",
                )

            return await ctx.send(embed=embed)

        if after and after.dt > now:
            await self.send_scheduled_close_message(ctx, after, silent)

        await thread.close(closer=ctx.author, after=close_after, message=message, silent=silent)

    @staticmethod
    def parse_user_or_role(ctx, user_or_role):
        mention = None
        if user_or_role is None:
            mention = ctx.author.mention
        elif hasattr(user_or_role, "mention"):
            mention = user_or_role.mention
        elif user_or_role in {"here", "everyone", "@here", "@everyone"}:
            mention = "@" + user_or_role.lstrip("@")
        return mention

    @commands.command(aliases=["alert"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def notify(
        self, ctx, *, user_or_role: Union[discord.Role, User, str.lower, None] = None
    ):
        """
        Desinscreve um usuário ou role da thread.

        Uma vez que uma mensagem é recebida, `user_or_role` será pingado uma vez.

        Deixe `user_or_role` vazio para notificar você mesmo
        `@here` e `@everyone` pode ser substituido por `here` e `everyone`.
        `user_or_role` pode ser um ID de usuário, menção, nome.
        """
        mention = self.parse_user_or_role(ctx, user_or_role)
        if mention is None:
            raise commands.BadArgument(f"{user_or_role} não é um utilizador ou role adequado")

        thread = ctx.thread

        if str(thread.id) not in self.bot.config["notification_squad"]:
            self.bot.config["notification_squad"][str(thread.id)] = []

        mentions = self.bot.config["notification_squad"][str(thread.id)]

        if mention in mentions:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=f"{mention} já será mencionado.",
            )
        else:
            mentions.append(mention)
            await self.bot.config.update()
            embed = discord.Embed(
                color=self.bot.main_color,
                description=f"{mention} será mencionado na próxima mensagem recebida.",
            )
        return await ctx.send(embed=embed)

    @commands.command(aliases=["unalert"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def unnotify(
        self, ctx, *, user_or_role: Union[discord.Role, User, str.lower, None] = None
    ):
        """
        Desinscreve um usuário ou role da thread.

        Deixe `user_or_role` vazio para desinscrever você mesmo
        `@here` e `@everyone` podem ser substituidas por `here` e `everyone`.
        `user_or_role` pode ser um ID de usuário, menção, nome.
        """
        mention = self.parse_user_or_role(ctx, user_or_role)
        if mention is None:
            mention = f"`{user_or_role}`"

        thread = ctx.thread

        if str(thread.id) not in self.bot.config["notification_squad"]:
            self.bot.config["notification_squad"][str(thread.id)] = []

        mentions = self.bot.config["notification_squad"][str(thread.id)]

        if mention not in mentions:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=f"{mention} não tem uma notificação pendente.",
            )
        else:
            mentions.remove(mention)
            await self.bot.config.update()
            embed = discord.Embed(
                color=self.bot.main_color, description=f"{mention} não será notificado."
            )
        return await ctx.send(embed=embed)

    @commands.command(aliases=["sub"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def subscribe(
        self, ctx, *, user_or_role: Union[discord.Role, User, str.lower, None] = None
    ):
        """
        Notifique um usuário, role da thread atual.

        Será notificado todas as vezes por cada mensagem recebida antes da desinscrição.

        Deixe `user_or_role` vazio para se notificar a si mesmo.
        `@here` e `@everyone` pode ser substituido por `here` e `everyone`.
        `user_or_role` pode ser um ID de usuário, menção, nome.
        """
        mention = self.parse_user_or_role(ctx, user_or_role)
        if mention is None:
            raise commands.BadArgument(f"{user_or_role} não é um usuário ou role correto.")

        thread = ctx.thread

        if str(thread.id) not in self.bot.config["subscriptions"]:
            self.bot.config["subscriptions"][str(thread.id)] = []

        mentions = self.bot.config["subscriptions"][str(thread.id)]

        if mention in mentions:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=f"{mention} não está subscrito nessa thread.",
            )
        else:
            mentions.append(mention)
            await self.bot.config.update()
            embed = discord.Embed(
                color=self.bot.main_color,
                description=f"{mention} will now be notified of all messages received. será notificado sobre todas as mensagens recebidas",
            )
        return await ctx.send(embed=embed)

    @commands.command(aliases=["unsub"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def unsubscribe(
        self, ctx, *, user_or_role: Union[discord.Role, User, str.lower, None] = None
    ):
        """
        Desinscreve um usuário ou role da thread.

        Deixe `user_or_role` vazio para se desinscrever
        `@here` e `@everyone` pode ser substituido por `here` e `everyone`.
        `user_or_role` pode ser um ID de usuário, menção, nome.
        """
        mention = self.parse_user_or_role(ctx, user_or_role)
        if mention is None:
            mention = f"`{user_or_role}`"

        thread = ctx.thread

        if str(thread.id) not in self.bot.config["subscriptions"]:
            self.bot.config["subscriptions"][str(thread.id)] = []

        mentions = self.bot.config["subscriptions"][str(thread.id)]

        if mention not in mentions:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=f"{mention} is not subscribed to this thread.",
            )
        else:
            mentions.remove(mention)
            await self.bot.config.update()
            embed = discord.Embed(
                color=self.bot.main_color,
                description=f"{mention} is now unsubscribed from this thread.",
            )
        return await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def nsfw(self, ctx):
        """Cria um aviso NSFW sobre a thread (not safe for work)."""
        await ctx.channel.edit(nsfw=True)
        sent_emoji, _ = await self.bot.retrieve_emoji()
        await self.bot.add_reaction(ctx.message, sent_emoji)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def sfw(self, ctx):
        """Classifica a thread como SFW (safe for work)"""
        await ctx.channel.edit(nsfw=False)
        sent_emoji, _ = await self.bot.retrieve_emoji()
        await self.bot.add_reaction(ctx.message, sent_emoji)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def loglink(self, ctx):
        """Providencia o link da database."""
        log_link = await self.bot.api.get_log_link(ctx.channel.id)
        await ctx.send(embed=discord.Embed(color=self.bot.main_color, description=log_link))

    def format_log_embeds(self, logs, avatar_url):
        embeds = []
        logs = tuple(logs)
        title = f"Total Results Found ({len(logs)})"

        for entry in logs:
            created_at = parser.parse(entry["created_at"])

            prefix = self.bot.config["log_url_prefix"].strip("/")
            if prefix == "NONE":
                prefix = ""
            log_url = f"{self.bot.config['log_url'].strip('/')}{'/' + prefix if prefix else ''}/{entry['key']}"

            username = entry["recipient"]["name"] + "#"
            username += entry["recipient"]["discriminator"]

            embed = discord.Embed(color=self.bot.main_color, timestamp=created_at)
            embed.set_author(name=f"{title} - {username}", icon_url=avatar_url, url=log_url)
            embed.url = log_url
            embed.add_field(name="Created", value=duration(created_at, now=datetime.utcnow()))
            closer = entry.get("closer")
            if closer is None:
                closer_msg = "Desconhecido"
            else:
                closer_msg = f"<@{closer['id']}>"
            embed.add_field(name="Fechado", value=closer_msg)

            if entry["recipient"]["id"] != entry["creator"]["id"]:
                embed.add_field(name="Created by", value=f"<@{entry['creator']['id']}>")

            embed.add_field(name="Preview", value=format_preview(entry["messages"]), inline=False)

            if closer is not None:
                # BUG: Currently, logviewer can't display logs without a closer.
                embed.add_field(name="Link", value=log_url)
            else:
                logger.debug("Invalid log entry: no closer.")
                embed.add_field(name="Log Key", value=f"`{entry['key']}`")

            embed.set_footer(text="Recipient ID: " + str(entry["recipient"]["id"]))
            embeds.append(embed)
        return embeds

    @commands.command(cooldown_after_parsing=True)
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    @commands.cooldown(1, 600, BucketType.channel)
    async def title(self, ctx, *, name: str):
        """Configura um título para a thread"""
        await ctx.thread.set_title(name)
        sent_emoji, _ = await self.bot.retrieve_emoji()
        await ctx.message.pin()
        await self.bot.add_reaction(ctx.message, sent_emoji)

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def logs(self, ctx, *, user: User = None):
        """
        Providencia as threads anteriores de um membro.

        Deixe `user` vazio quando este comando for usado em um
        canal de ticket para mostrar as logs correntes.
        `user_or_role` pode ser um ID de usuário, menção, nome.
        """

        await ctx.trigger_typing()

        if not user:
            thread = ctx.thread
            if not thread:
                raise commands.MissingRequiredArgument(SimpleNamespace(name="member"))
            user = thread.recipient or await self.bot.fetch_user(thread.id)

        default_avatar = "https://cdn.discordapp.com/embed/avatars/0.png"
        icon_url = getattr(user, "avatar_url", default_avatar)

        logs = await self.bot.api.get_user_logs(user.id)

        if not any(not log["open"] for log in logs):
            embed = discord.Embed(
                color=self.bot.error_color,
                description="This user does not have any previous logs.",
            )
            return await ctx.send(embed=embed)

        logs = reversed([log for log in logs if not log["open"]])

        embeds = self.format_log_embeds(logs, avatar_url=icon_url)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @logs.command(name="closed-by", aliases=["closeby"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def logs_closed_by(self, ctx, *, user: User = None):
        """
        Providencia todas as logs fechadas por um determinado utilizador.

        Se nenhum `user` for providenciado, a pessoa será quem usou este comando.
        `user_or_role` pode ser um ID de usuário, menção, nome.
        """
        user = user if user is not None else ctx.author

        entries = await self.bot.api.search_closed_by(user.id)
        embeds = self.format_log_embeds(entries, avatar_url=self.bot.guild.icon_url)

        if not embeds:
            embed = discord.Embed(
                color=self.bot.error_color,
                description="Nenhuma log encontrada.",
            )
            return await ctx.send(embed=embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @logs.command(name="delete", aliases=["wipe"])
    @checks.has_permissions(PermissionLevel.OWNER)
    async def logs_delete(self, ctx, key_or_link: str):
        """
        Limpa uma log da database criada.
        """
        key = key_or_link.split("/")[-1]

        success = await self.bot.api.delete_log_entry(key)

        if not success:
            embed = discord.Embed(
                title="Error",
                description=f"Log entry `{key}` not found.",
                color=self.bot.error_color,
            )
        else:
            embed = discord.Embed(
                title="Success",
                description=f"Log entry `{key}` successfully deleted.",
                color=self.bot.main_color,
            )

        await ctx.send(embed=embed)

    @logs.command(name="responded")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def logs_responded(self, ctx, *, user: User = None):
        """
        Providencia todas as logs de um utilizador.

        Se nenhum `user` for providenciado, será usado o usuário que usou este comando.
        `user_or_role` pode ser um ID de usuário, menção, nome.
        """
        user = user if user is not None else ctx.author

        entries = await self.bot.api.get_responded_logs(user.id)

        embeds = self.format_log_embeds(entries, avatar_url=self.bot.guild.icon_url)

        if not embeds:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=f"{getattr(user, 'mention', user.id)} has not responded to any threads.",
            )
            return await ctx.send(embed=embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @logs.command(name="search", aliases=["find"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def logs_search(self, ctx, limit: Optional[int] = None, *, query):
        """
        Providencia todas as logs de acordo com a sua pesquisa.

        Providencie um `limit` para encontrar um número máximo de logs a serem encontradas.
        """

        await ctx.trigger_typing()

        entries = await self.bot.api.search_by_text(query, limit)

        embeds = self.format_log_embeds(entries, avatar_url=self.bot.guild.icon_url)

        if not embeds:
            embed = discord.Embed(
                color=self.bot.error_color,
                description="Nenhuma log encontrada.",
            )
            return await ctx.send(embed=embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def reply(self, ctx, *, msg: str = ""):
        """
        Responde a uma thread.

        Suporta anexos assim como permite
        automaticamente criar embed de URLs.
        """
        ctx.message.content = msg
        async with ctx.typing():
            await ctx.thread.reply(ctx.message)

    @commands.command(aliases=["formatreply"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def freply(self, ctx, *, msg: str = ""):
        """
        Responde a uma thread com variáveis.

       Funciona como `?reply`, mas com a adição de três variáveis
          - `{{channel}}` - o `discord.TextChannel` objeto
          - `{{recipient}}` - o `discord.User` objeto do recipiente
          - `{{author}}` - the `discord.User` object do autor

        Suporta anexos assim como suporta
        embeds de URLs automáticas.
        """
        msg = self.bot.formatter.format(
            msg, channel=ctx.channel, recipient=ctx.thread.recipient, author=ctx.message.author
        )
        ctx.message.content = msg
        async with ctx.typing():
            await ctx.thread.reply(ctx.message)

    @commands.command(aliases=["formatanonreply"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def fareply(self, ctx, *, msg: str = ""):
        """
        Responde a uma thread com variáveis e anonimamente.

       Funciona como `?reply`, mas com a adição de três variáveis
          - `{{channel}}` - o `discord.TextChannel` objeto
          - `{{recipient}}` - o `discord.User` objeto do recipiente
          - `{{author}}` - the `discord.User` object do autor

        Suporta anexos assim como suporta
        embeds de URLs automáticas.
        """
        msg = self.bot.formatter.format(
            msg, channel=ctx.channel, recipient=ctx.thread.recipient, author=ctx.message.author
        )
        ctx.message.content = msg
        async with ctx.typing():
            await ctx.thread.reply(ctx.message, anonymous=True)

    @commands.command(aliases=["anonreply", "anonymousreply"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def areply(self, ctx, *, msg: str = ""):
        """
        Responder a uma thread anonimamente.

        É possível mudar o nome do anónimo,
        avatar e tag usando o comando config.

        Edite o `anon_username`, `anon_avatar_url`
        e `anon_tag` para fazer isso.
        """
        ctx.message.content = msg
        async with ctx.typing():
            await ctx.thread.reply(ctx.message, anonymous=True)

    @commands.command(aliases=["plainreply"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def preply(self, ctx, *, msg: str = ""):
        """
        Responder a uma thread planamente (sem prefixo).

        Suporta anexos assim como suporta
        embeds de URLs automáticas.
        """
        ctx.message.content = msg
        async with ctx.typing():
            await ctx.thread.reply(ctx.message, plain=True)

    @commands.command(aliases=["plainanonreply", "plainanonymousreply"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def pareply(self, ctx, *, msg: str = ""):
        """
        Responder a uma thread anonimamente e sem prefixo.

        Supports attachments and images as well as
        automatically embedding image URLs.
        """
        ctx.message.content = msg
        async with ctx.typing():
            await ctx.thread.reply(ctx.message, anonymous=True, plain=True)

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def note(self, ctx, *, msg: str = ""):
        """
        Cria uma nota sobre a thread aparente.

        Útil para contextos de anotação.
        """
        ctx.message.content = msg
        async with ctx.typing():
            msg = await ctx.thread.note(ctx.message)
            await msg.pin()

    @note.command(name="persistent", aliases=["persist"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def note_persistent(self, ctx, *, msg: str = ""):
        """
        Tira uma nota persistente sobre o usuário atual.
        """
        ctx.message.content = msg
        async with ctx.typing():
            msg = await ctx.thread.note(ctx.message, persistent=True)
            await msg.pin()
        await self.bot.api.create_note(
            recipient=ctx.thread.recipient, message=ctx.message, message_id=msg.id
        )

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def edit(self, ctx, message_id: Optional[int] = None, *, message: str):
        """ 
        Edita uma mensagem.

        Se nenhum `message_id` é providenciado,
        a última mensagem será editada.

        Nota: attachments **não podem** ser editados..
        """
        thread = ctx.thread

        try:
            await thread.edit_message(message_id, message)
        except ValueError:
            return await ctx.send(
                embed=discord.Embed(
                    title="Falhado",
                    description="Não foi possível encontrar uma mensagem para editar    .",
                    color=self.bot.error_color,
                )
            )

        sent_emoji, _ = await self.bot.retrieve_emoji()
        await self.bot.add_reaction(ctx.message, sent_emoji)

    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def selfcontact(self, ctx):
        """Cria uma thread para si mesmo"""
        await ctx.invoke(self.contact, user=ctx.author)

    @commands.command(usage="<user> [category] [options]")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def contact(
        self,
        ctx,
        user: Union[discord.Member, discord.User],
        *,
        category: Union[SimilarCategoryConverter, str] = None,
        manual_trigger=True,
    ):
        """
        Cria uma thread com um membro especificado.

        If `category` is specified, the thread
        will be created in that specified category.

        `category`, if specified, may be a category ID, mention, or name.
        `user` may be a user ID, mention, or name.
        `options` can be `silent`
        """
        silent = False
        if isinstance(category, str):
            if "silent" in category or "silently" in category:
                silent = True
            category = None

        if user.bot:
            embed = discord.Embed(
                color=self.bot.error_color, description="Cannot start a thread with a bot."
            )
            return await ctx.send(embed=embed, delete_afer=3)

        exists = await self.bot.threads.find(recipient=user)
        if exists:
            embed = discord.Embed(
                color=self.bot.error_color,
                description="Uma thread para este usuário já"
                f"existe em {exists.channel.mention}.",
            )
            await ctx.channel.send(embed=embed, delete_after=3)

        else:
            thread = await self.bot.threads.create(user, creator=ctx.author, category=category)
            if self.bot.config["dm_disabled"] in (DMDisabled.NEW_THREADS, DMDisabled.ALL_THREADS):
                logger.info("Contacting user %s when Modmail DM is disabled.", user)

            if not silent and not self.bot.config.get("thread_contact_silently"):
                if ctx.author.id == user.id:
                    description = "Uma thread foi aberta."
                else:
                    description = f"{ctx.author.name} abriu uma thread."

                em = discord.Embed(
                    title="New Thread", description=description, color=self.bot.main_color,
                )
                if self.bot.config["show_timestamp"]:
                    em.timestamp = datetime.utcnow()
                em.set_footer(icon_url=ctx.author.avatar_url)
                await user.send(embed=em)

            embed = discord.Embed(
                title="Thread criada",
                description=f"Thread criada  {ctx.author.mention} para {user.mention}.",
                color=self.bot.main_color,
            )
            await thread.wait_until_ready()
            await thread.channel.send(embed=embed)

            if manual_trigger:
                sent_emoji, _ = await self.bot.retrieve_emoji()
                await self.bot.add_reaction(ctx.message, sent_emoji)
                await asyncio.sleep(5)
                await ctx.message.delete()

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @trigger_typing
    async def blocked(self, ctx):
        """Providencia uma lista de utilizadores e/ou roles bloqueadas."""

        embeds = [discord.Embed(title="Blocked Users", color=self.bot.main_color, description="")]

        roles = []
        users = []
        now = ctx.message.created_at

        blocked_users = list(self.bot.blocked_users.items())
        for id_, reason in blocked_users:
            # parse "reason" and check if block is expired
            # etc "blah blah blah... until 2019-10-14T21:12:45.559948."
            end_time = re.search(r"until ([^`]+?)\.$", reason)
            if end_time is None:
                # backwards compat
                end_time = re.search(r"%([^%]+?)%", reason)
                if end_time is not None:
                    logger.warning(
                        r"Deprecated time message for user %s, block and unblock again to update.",
                        id_,
                    )

            if end_time is not None:
                after = (datetime.fromisoformat(end_time.group(1)) - now).total_seconds()
                if after <= 0:
                    # No longer blocked
                    self.bot.blocked_users.pop(str(id_))
                    logger.debug("No longer blocked, user %s.", id_)
                    continue

            user = self.bot.get_user(int(id_))
            if user:
                users.append((user.mention, reason))
            else:
                try:
                    user = await self.bot.fetch_user(id_)
                    users.append((user.mention, reason))
                except discord.NotFound:
                    users.append((id_, reason))

        blocked_roles = list(self.bot.blocked_roles.items())
        for id_, reason in blocked_roles:
            # parse "reason" and check if block is expired
            # etc "blah blah blah... until 2019-10-14T21:12:45.559948."
            end_time = re.search(r"until ([^`]+?)\.$", reason)
            if end_time is None:
                # backwards compat
                end_time = re.search(r"%([^%]+?)%", reason)
                if end_time is not None:
                    logger.warning(
                        r"Deprecated time message for role %s, block and unblock again to update.",
                        id_,
                    )

            if end_time is not None:
                after = (datetime.fromisoformat(end_time.group(1)) - now).total_seconds()
                if after <= 0:
                    # No longer blocked
                    self.bot.blocked_roles.pop(str(id_))
                    logger.debug("No longer blocked, role %s.", id_)
                    continue

            role = self.bot.guild.get_role(int(id_))
            if role:
                roles.append((role.mention, reason))

        if users:
            embed = embeds[0]

            for mention, reason in users:
                line = mention + f" - {reason or 'No Reason Provided'}\n"
                if len(embed.description) + len(line) > 2048:
                    embed = discord.Embed(
                        title="Blocked Users (Continued)",
                        color=self.bot.main_color,
                        description=line,
                    )
                    embeds.append(embed)
                else:
                    embed.description += line
        else:
            embeds[0].description = "Currently there are no blocked users."

        embeds.append(
            discord.Embed(title="Blocked Roles", color=self.bot.main_color, description="")
        )

        if roles:
            embed = embeds[-1]

            for mention, reason in roles:
                line = mention + f" - {reason or 'No Reason Provided'}\n"
                if len(embed.description) + len(line) > 2048:
                    embed = discord.Embed(
                        title="Blocked Roles (Continued)",
                        color=self.bot.main_color,
                        description=line,
                    )
                    embeds.append(embed)
                else:
                    embed.description += line
        else:
            embeds[-1].description = "Currently there are no blocked roles."

        session = EmbedPaginatorSession(ctx, *embeds)

        await session.run()

    @blocked.command(name="whitelist")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @trigger_typing
    async def blocked_whitelist(self, ctx, *, user: User = None):
        """
        Whitelist or un-whitelist a user from getting blocked.

        Useful for preventing users from getting blocked by account_age/guild_age restrictions.
        """
        if user is None:
            thread = ctx.thread
            if thread:
                user = thread.recipient
            else:
                return await ctx.send_help(ctx.command)

        mention = getattr(user, "mention", f"`{user.id}`")
        msg = ""

        if str(user.id) in self.bot.blocked_whitelisted_users:
            embed = discord.Embed(
                title="Success",
                description=f"{mention} is no longer whitelisted.",
                color=self.bot.main_color,
            )
            self.bot.blocked_whitelisted_users.remove(str(user.id))
            return await ctx.send(embed=embed)

        self.bot.blocked_whitelisted_users.append(str(user.id))

        if str(user.id) in self.bot.blocked_users:
            msg = self.bot.blocked_users.get(str(user.id)) or ""
            self.bot.blocked_users.pop(str(user.id))

        await self.bot.config.update()

        if msg.startswith("System Message: "):
            # If the user is blocked internally (for example: below minimum account age)
            # Show an extended message stating the original internal message
            reason = msg[16:].strip().rstrip(".")
            embed = discord.Embed(
                title="Success",
                description=f"{mention} was previously blocked internally for "
                f'"{reason}". {mention} is now whitelisted.',
                color=self.bot.main_color,
            )
        else:
            embed = discord.Embed(
                title="Success",
                color=self.bot.main_color,
                description=f"{mention} is now whitelisted.",
            )

        return await ctx.send(embed=embed)

    @commands.command(usage="[user] [duration] [reason]")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @trigger_typing
    async def block(
        self,
        ctx,
        user_or_role: Optional[Union[User, discord.Role]] = None,
        *,
        after: UserFriendlyTime = None,
    ):
        """
        Bloqueia um utilizador ou role da utilização do suporte.

        You may choose to set a time as to when the user will automatically be unblocked.

        Leave `user` blank when this command is used within a
        thread channel to block the current recipient.
        `user` may be a user ID, mention, or name.
        `duration` may be a simple "human-readable" time text. See `{prefix}help close` for examples.
        """

        if user_or_role is None:
            thread = ctx.thread
            if thread:
                user_or_role = thread.recipient
            elif after is None:
                raise commands.MissingRequiredArgument(SimpleNamespace(name="user or role"))
            else:
                raise commands.BadArgument(f'User or role "{after.arg}" not found.')

        mention = getattr(user_or_role, "mention", f"`{user_or_role.id}`")

        if (
            not isinstance(user_or_role, discord.Role)
            and str(user_or_role.id) in self.bot.blocked_whitelisted_users
        ):
            embed = discord.Embed(
                title="Error",
                description=f"Cannot block {mention}, user is whitelisted.",
                color=self.bot.error_color,
            )
            return await ctx.send(embed=embed)

        reason = f"by {escape_markdown(ctx.author.name)}#{ctx.author.discriminator}"

        if after is not None:
            if "%" in reason:
                raise commands.BadArgument('The reason contains illegal character "%".')
            if after.arg:
                reason += f" for `{after.arg}`"
            if after.dt > after.now:
                reason += f" until {after.dt.isoformat()}"

        reason += "."

        if isinstance(user_or_role, discord.Role):
            msg = self.bot.blocked_roles.get(str(user_or_role.id))
        else:
            msg = self.bot.blocked_users.get(str(user_or_role.id))

        if msg is None:
            msg = ""

        if msg:
            old_reason = msg.strip().rstrip(".")
            embed = discord.Embed(
                title="Success",
                description=f"{mention} was previously blocked {old_reason}.\n"
                f"{mention} is now blocked {reason}",
                color=self.bot.main_color,
            )
        else:
            embed = discord.Embed(
                title="Success",
                color=self.bot.main_color,
                description=f"{mention} is now blocked {reason}",
            )

        if isinstance(user_or_role, discord.Role):
            self.bot.blocked_roles[str(user_or_role.id)] = reason
        else:
            self.bot.blocked_users[str(user_or_role.id)] = reason
        await self.bot.config.update()

        return await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @trigger_typing
    async def unblock(self, ctx, *, user_or_role: Union[User, Role] = None):
        """
        Desbloqueia um utilizador do suporte.

        Leave `user` blank when this command is used within a
        thread channel to unblock the current recipient.
        `user` may be a user ID, mention, or name.
        """

        if user_or_role is None:
            thread = ctx.thread
            if thread:
                user_or_role = thread.recipient
            else:
                raise commands.MissingRequiredArgument(SimpleNamespace(name="user"))

        mention = getattr(user_or_role, "mention", f"`{user_or_role.id}`")
        name = getattr(user_or_role, "name", f"`{user_or_role.id}`")

        if (
            not isinstance(user_or_role, discord.Role)
            and str(user_or_role.id) in self.bot.blocked_users
        ):
            msg = self.bot.blocked_users.pop(str(user_or_role.id)) or ""
            await self.bot.config.update()

            if msg.startswith("System Message: "):
                # If the user is blocked internally (for example: below minimum account age)
                # Show an extended message stating the original internal message
                reason = msg[16:].strip().rstrip(".") or "no reason"
                embed = discord.Embed(
                    title="Success",
                    description=f"{mention} was previously blocked internally {reason}.\n"
                    f"{mention} is no longer blocked.",
                    color=self.bot.main_color,
                )
                embed.set_footer(
                    text="However, if the original system block reason still applies, "
                    f"{name} will be automatically blocked again. "
                    f'Use "{self.bot.prefix}blocked whitelist {user_or_role.id}" to whitelist the user.'
                )
            else:
                embed = discord.Embed(
                    title="Success",
                    color=self.bot.main_color,
                    description=f"{mention} is no longer blocked.",
                )
        elif (
            isinstance(user_or_role, discord.Role)
            and str(user_or_role.id) in self.bot.blocked_roles
        ):
            msg = self.bot.blocked_roles.pop(str(user_or_role.id)) or ""
            await self.bot.config.update()

            embed = discord.Embed(
                title="Success",
                color=self.bot.main_color,
                description=f"{mention} is no longer blocked.",
            )
        else:
            embed = discord.Embed(
                title="Error", description=f"{mention} is not blocked.", color=self.bot.error_color
            )

        return await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def delete(self, ctx, message_id: int = None):
        """
        Deleta uma mensagem.

        Deletes the previous message, unless a message ID is provided,
        which in that case, deletes the message with that message ID.

        Notes can only be deleted when a note ID is provided.
        """
        thread = ctx.thread

        try:
            await thread.delete_message(message_id, note=True)
        except ValueError as e:
            logger.warning("Failed to delete message: %s.", e)
            return await ctx.send(
                embed=discord.Embed(
                    title="Failed",
                    description="Cannot find a message to delete. Plain messages are not supported.",
                    color=self.bot.error_color,
                )
            )

        sent_emoji, _ = await self.bot.retrieve_emoji()
        await self.bot.add_reaction(ctx.message, sent_emoji)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def repair(self, ctx):
        """
        Repara uma threada quebrada pelo discord.
        """
        sent_emoji, blocked_emoji = await self.bot.retrieve_emoji()

        if ctx.thread:
            user_id = match_user_id(ctx.channel.topic)
            if user_id == -1:
                logger.info("Setting current channel's topic to User ID.")
                await ctx.channel.edit(topic=f"User ID: {ctx.thread.id}")
            return await self.bot.add_reaction(ctx.message, sent_emoji)

        logger.info("Attempting to fix a broken thread %s.", ctx.channel.name)

        # Search cache for channel
        user_id, thread = next(
            ((k, v) for k, v in self.bot.threads.cache.items() if v.channel == ctx.channel),
            (-1, None),
        )
        if thread is not None:
            logger.debug("Found thread with tempered ID.")
            await ctx.channel.edit(reason="Fix broken Modmail thread", topic=f"User ID: {user_id}")
            return await self.bot.add_reaction(ctx.message, sent_emoji)

        # find genesis message to retrieve User ID
        async for message in ctx.channel.history(limit=10, oldest_first=True):
            if (
                message.author == self.bot.user
                and message.embeds
                and message.embeds[0].color
                and message.embeds[0].color.value == self.bot.main_color
                and message.embeds[0].footer.text
            ):
                user_id = match_user_id(message.embeds[0].footer.text)
                if user_id != -1:
                    recipient = self.bot.get_user(user_id)
                    if recipient is None:
                        self.bot.threads.cache[user_id] = thread = Thread(
                            self.bot.threads, user_id, ctx.channel
                        )
                    else:
                        self.bot.threads.cache[user_id] = thread = Thread(
                            self.bot.threads, recipient, ctx.channel
                        )
                    thread.ready = True
                    logger.info(
                        "Setting current channel's topic to User ID and created new thread."
                    )
                    await ctx.channel.edit(
                        reason="Fix broken Modmail thread", topic=f"User ID: {user_id}"
                    )
                    return await self.bot.add_reaction(ctx.message, sent_emoji)

        else:
            logger.warning("No genesis message found.")

        # match username from channel name
        # username-1234, username-1234_1, username-1234_2
        m = re.match(r"^(.+)-(\d{4})(?:_\d+)?$", ctx.channel.name)
        if m is not None:
            users = set(
                filter(
                    lambda member: member.name == m.group(1)
                    and member.discriminator == m.group(2),
                    ctx.guild.members,
                )
            )
            if len(users) == 1:
                user = users.pop()
                name = format_channel_name(
                    user, self.bot.modmail_guild, exclude_channel=ctx.channel
                )
                recipient = self.bot.get_user(user.id)
                if user.id in self.bot.threads.cache:
                    thread = self.bot.threads.cache[user.id]
                    if thread.channel:
                        embed = discord.Embed(
                            title="Delete Channel",
                            description="This thread channel is no longer in use. "
                            f"All messages will be directed to {ctx.channel.mention} instead.",
                            color=self.bot.error_color,
                        )
                        embed.set_footer(
                            text='Please manually delete this channel, do not use "{prefix}close".'
                        )
                        try:
                            await thread.channel.send(embed=embed)
                        except discord.HTTPException:
                            pass
                if recipient is None:
                    self.bot.threads.cache[user.id] = thread = Thread(
                        self.bot.threads, user_id, ctx.channel
                    )
                else:
                    self.bot.threads.cache[user.id] = thread = Thread(
                        self.bot.threads, recipient, ctx.channel
                    )
                thread.ready = True
                logger.info("Setting current channel's topic to User ID and created new thread.")
                await ctx.channel.edit(
                    reason="Fix broken Modmail thread", name=name, topic=f"User ID: {user.id}"
                )
                return await self.bot.add_reaction(ctx.message, sent_emoji)

            elif len(users) >= 2:
                logger.info("Multiple users with the same name and discriminator.")
        return await self.bot.add_reaction(ctx.message, blocked_emoji)

    @commands.command()
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def enable(self, ctx):
        """
        Habilita as funcionalidades do suporte.

        Undo's the `{prefix}disable` command, all DM will be relayed after running this command.
        """
        embed = discord.Embed(
            title="Success",
            description="Modmail will now accept all DM messages.",
            color=self.bot.main_color,
        )

        if self.bot.config["dm_disabled"] != DMDisabled.NONE:
            self.bot.config["dm_disabled"] = DMDisabled.NONE
            await self.bot.config.update()

        return await ctx.send(embed=embed)

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def disable(self, ctx):
        """
        Desabilita funções do suporte.

        To stop all new threads from being created, do `{prefix}disable new`.
        To stop all existing threads from DMing Modmail, do `{prefix}disable all`.
        To check if the DM function for Modmail is enabled, do `{prefix}isenable`.
        """
        await ctx.send_help(ctx.command)

    @disable.command(name="new")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def disable_new(self, ctx):
        """
        Stop accepting new Modmail threads.

        No new threads can be created through DM.
        """
        embed = discord.Embed(
            title="Success",
            description="Modmail will not create any new threads.",
            color=self.bot.main_color,
        )
        if self.bot.config["dm_disabled"] < DMDisabled.NEW_THREADS:
            self.bot.config["dm_disabled"] = DMDisabled.NEW_THREADS
            await self.bot.config.update()

        return await ctx.send(embed=embed)

    @disable.command(name="all")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def disable_all(self, ctx):
        """
        Disables all DM functionalities of Modmail.

        No new threads can be created through DM nor no further DM messages will be relayed.
        """
        embed = discord.Embed(
            title="Success",
            description="Modmail will not accept any DM messages.",
            color=self.bot.main_color,
        )

        if self.bot.config["dm_disabled"] != DMDisabled.ALL_THREADS:
            self.bot.config["dm_disabled"] = DMDisabled.ALL_THREADS
            await self.bot.config.update()

        return await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def isenable(self, ctx):
        """
        Verifica se as funcionalidades do suporte estão ativadas.
        """

        if self.bot.config["dm_disabled"] == DMDisabled.NEW_THREADS:
            embed = discord.Embed(
                title="New Threads Disabled",
                description="Modmail is not creating new threads.",
                color=self.bot.error_color,
            )
        elif self.bot.config["dm_disabled"] == DMDisabled.ALL_THREADS:
            embed = discord.Embed(
                title="All DM Disabled",
                description="Modmail is not accepting any DM messages for new and existing threads.",
                color=self.bot.error_color,
            )
        else:
            embed = discord.Embed(
                title="Enabled",
                description="Modmail now is accepting all DM messages.",
                color=self.bot.main_color,
            )

        return await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(Modmail(bot))
