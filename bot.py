import discord
from discord.ext import commands, tasks
import os
import re
import asyncio
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv
import database as db

load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
SAO_PAULO_TZ = pytz.timezone('America/Sao_Paulo')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)


# ─── Helpers ────────────────────────────────────────────────────────────────

def is_competition_active() -> bool:
    now = datetime.now(SAO_PAULO_TZ)
    wd = now.weekday()  # 0=Mon … 6=Sun
    h = now.hour
    if wd == 0:       return h >= 6        # Segunda a partir das 6h
    if wd in (1, 2, 3): return True        # Ter, Qua, Qui: o dia todo
    if wd == 4:       return h < 17        # Sexta até 17h
    return False                           # Sab / Dom


def parse_guessthegame(content: str) -> tuple[int, int] | None:
    """
    Extrai (número_do_jogo, pontuação) de um resultado do GuessTheGame.
    Retorna None se a mensagem não for um resultado válido.
    """
    # Verifica se é um resultado do GuessTheGame
    if not re.search(r'#GuessTheGame', content, re.IGNORECASE):
        return None

    # Extrai o número do jogo — tenta vários formatos
    num_match = (
        re.search(r'#GuessTheGame\s*#?\s*(\d+)', content, re.IGNORECASE)  # #GuessTheGame #1414
        or re.search(r'guessthe\.game/p/(\d+)', content, re.IGNORECASE)    # URL fallback
        or re.search(r'#(\d+)', content)                                    # qualquer #número
    )
    if not num_match:
        return None

    game_number = int(num_match.group(1))

    GREEN = '🟩'
    WRONG_CHARS = {'🟥', '🟨'}

    sequence = []
    for ch in content:
        if ch == GREEN:
            sequence.append('green')
        elif ch in WRONG_CHARS:
            sequence.append('wrong')

    if not sequence:
        return None

    if 'green' not in sequence:
        return (game_number, 0)  # Não acertou

    wrong_before = sequence.index('green')
    score = max(1, 6 - wrong_before)
    return (game_number, score)



def week_start_str() -> str:
    """Retorna a string ISO da segunda-feira às 6h da semana atual."""
    now = datetime.now(SAO_PAULO_TZ)
    monday = now - timedelta(days=now.weekday())
    monday = monday.replace(hour=6, minute=0, second=0, microsecond=0)
    if now < monday:
        monday -= timedelta(weeks=1)
    return monday.isoformat()


# ─── Events ─────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f'✅  GuessTheBot online como {bot.user}  (discord.py {discord.__version__})')
    db.init()
    weekly_reset_check.start()
    friday_ranking_check.start()


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    guild_id = str(message.guild.id)
    user_id = str(message.author.id)
    content_lower = message.content.lower()

    has_perm = (
        message.author.guild_permissions.administrator
        or message.author.guild_permissions.manage_channels
    )

    # ── Comandos via @menção ────────────────────────────────────────────────
    if bot.user in message.mentions:

        if 'guessthegamehere' in content_lower:
            if not has_perm:
                await message.reply('❌ Você precisa ser administrador ou ter permissão de gerenciar canais.')
                return
            db.set_channel(guild_id, str(message.channel.id))
            await message.reply(
                '✅ **Canal configurado!**\n'
                'Agora vou monitorar os resultados do GuessTheGame aqui.\n'
                'A competição roda de segunda às 6h até sexta às 17h (horário de São Paulo).'
            )
            return

        if 'resetthegame' in content_lower:
            if not has_perm:
                await message.reply('❌ Você precisa ser administrador ou ter permissão de gerenciar canais.')
                return

            confirm = await message.reply(
                '⚠️ **Tem certeza?** Isso vai zerar **todos os pontos da semana atual**.\n'
                'Reaja com ✅ para confirmar ou ❌ para cancelar.'
            )
            await confirm.add_reaction('✅')
            await confirm.add_reaction('❌')

            def check(reaction, user):
                return (
                    user == message.author
                    and str(reaction.emoji) in ('✅', '❌')
                    and reaction.message.id == confirm.id
                )

            try:
                reaction, _ = await bot.wait_for('reaction_add', timeout=30.0, check=check)
                if str(reaction.emoji) == '✅':
                    db.reset_scores(guild_id)
                    await confirm.reply('🔄 Pontuação da semana zerada com sucesso! Nova competição começou.')
                else:
                    await confirm.reply('❌ Reset cancelado.')
            except asyncio.TimeoutError:
                await confirm.reply('⏰ Tempo esgotado. Reset cancelado.')
            return

        return  # Menção sem comando reconhecido — ignora

    # ── Monitoramento do canal ──────────────────────────────────────────────
    channel_id = db.get_channel(guild_id)
    print(f'[DEBUG] guild={guild_id} channel_atual={message.channel.id} channel_config={channel_id}')
    if not channel_id or str(message.channel.id) != channel_id:
        print(f'[DEBUG] canal ignorado')
        return

    result = parse_guessthegame(message.content)
    print(f'[DEBUG] parse_result={result} competition_active={is_competition_active()}')


    if not is_competition_active():
        if result is not None:
            now = datetime.now(SAO_PAULO_TZ)
            wd = now.weekday()
            if wd in (5, 6):
                when = 'segunda-feira às 6h'
            elif wd == 4:
                when = 'terminou hoje às 17h. A próxima começa na segunda-feira às 6h'
            else:
                when = 'ainda não começou hoje. Começa às 6h'
            await message.reply(
                f'⏸️ A competição {when} (horário de São Paulo). '
                f'Seu resultado do jogo **#{result[0]}** não foi contabilizado.'
            )
        return

    if result is None:
        return

    game_number, daily_score = result

    if db.has_submission(guild_id, user_id, game_number):
        await message.reply(f'⚠️ Você já enviou o resultado do jogo **#{game_number}** essa semana!')
        return

    db.add_score(guild_id, user_id, message.author.display_name, game_number, daily_score)
    weekly_total = db.get_weekly_total(guild_id, user_id)

    if daily_score == 0:
        msg = (
            f'😔 Não foi dessa vez no jogo **#{game_number}**. **+0 pts**\n'
            f'📊 Total na semana: **{weekly_total} pts**'
        )
    elif daily_score == 6:
        msg = (
            f'🏆 Incrível! Primeira tentativa no jogo **#{game_number}**! **+6 pts**\n'
            f'📊 Total na semana: **{weekly_total} pts**'
        )
    else:
        guesses = 7 - daily_score
        msg = (
            f'🎮 Acertou em **{guesses}ª tentativa** no jogo **#{game_number}**! **+{daily_score} pts**\n'
            f'📊 Total na semana: **{weekly_total} pts**'
        )

    await message.reply(msg)


# ─── Tarefas agendadas ───────────────────────────────────────────────────────

@tasks.loop(minutes=1)
async def weekly_reset_check():
    now = datetime.now(SAO_PAULO_TZ)
    if now.weekday() == 0 and now.hour == 6 and now.minute == 0:
        for guild in bot.guilds:
            gid = str(guild.id)
            db.reset_scores(gid)
            ch_id = db.get_channel(gid)
            if ch_id:
                ch = bot.get_channel(int(ch_id))
                if ch:
                    await ch.send(
                        '🏁 **Nova semana de competição começou!**\n'
                        'Compartilhe seus resultados do GuessTheGame aqui! 🎮'
                    )


@tasks.loop(minutes=1)
async def friday_ranking_check():
    now = datetime.now(SAO_PAULO_TZ)
    if now.weekday() == 4 and now.hour == 17 and now.minute == 0:
        for guild in bot.guilds:
            await post_ranking(guild)


async def post_ranking(guild: discord.Guild):
    gid = str(guild.id)
    ch_id = db.get_channel(gid)
    if not ch_id:
        return
    ch = bot.get_channel(int(ch_id))
    if not ch:
        return

    scores = db.get_ranking(gid)
    medals = ['🥇', '🥈', '🥉']

    embed = discord.Embed(
        title='🏆 Ranking da Semana — GuessTheGame',
        color=0xFFD700,
        timestamp=datetime.now(SAO_PAULO_TZ)
    )

    if not scores:
        embed.description = 'Nenhum participante pontuou essa semana. 😢'
    else:
        lines = []
        for i, (name, total) in enumerate(scores):
            prefix = medals[i] if i < 3 else f'`{i + 1}.`'
            lines.append(f'{prefix} **{name}** — {total} pts')
        embed.description = '\n'.join(lines)

    embed.set_footer(text='Reinicia na segunda-feira às 6h • Horário de São Paulo')
    await ch.send(embed=embed)


# ─── Start ───────────────────────────────────────────────────────────────────

if not TOKEN:
    raise ValueError('DISCORD_TOKEN não definido no arquivo .env')

bot.run(TOKEN)
