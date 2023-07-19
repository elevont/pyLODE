from dominate.tags import tr, td, p, a, strong


@tr
def metadata_row(key: str, value: str | list[str], value_is_link: bool = False) -> td:
    with td(
        _class="tableblock halign-right valign-top",
        style="background-color: #FFFFFF;",
    ) as component:
        if isinstance(value, str):
            if value_is_link:
                with p(f"{key}: ", _class="tableblock"):
                    with strong():
                        a(value, href=value, _class="bare")
            else:
                p(
                    f"{key}: {value}",
                    _class="tableblock",
                )
        else:
            if value_is_link:
                with p(f"{key}: ", _class="tableblock"):
                    for v in value:
                        with strong():
                            a(v, href=v, _class="bare")
            else:
                p_value = ", ".join(value)
                p(f"{key}: {p_value}", _class="tableblock")

        return component
