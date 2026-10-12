def get_sender(message):
    return get_username(message.from_user)


def get_username(user):
    if user.username:
        return f"@{user.username}"

    name = " ".join(part for part in (user.first_name, user.last_name) if part)
    return f"{name or 'Без имени'} (ID: {user.id})"


def get_name(user):
    name = " ".join(part for part in (user.first_name, user.last_name) if part)

    if user.username:
        return f"{name} (@{user.username})" if name else f"@{user.username}"

    return name or f"ID: {user.id}"
